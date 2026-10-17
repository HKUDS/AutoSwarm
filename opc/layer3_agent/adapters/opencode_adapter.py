"""OpenCode CLI adapter."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

from opc.core.models import AgentStatus, ApprovalDecision, Task, TaskResult, TaskStatus
from opc.layer3_agent.adapters.base import (
    ExternalAgentAdapter,
    ExternalAgentStdinPolicy,
    ExternalApprovalRequest,
)


class OpenCodeAdapter(ExternalAgentAdapter):
    """Invokes the OpenCode CLI via ``opencode run``."""

    agent_type = "opencode"
    default_command = "opencode"
    _permission_handler_support_cache: dict[str, bool] = {}

    def __init__(self, config=None) -> None:
        super().__init__(config=config)
        self._process: asyncio.subprocess.Process | None = None

    def resolve_binary(self) -> str | None:
        if not self.config.enabled:
            return None
        for candidate in self._candidate_commands():
            resolved = self._resolve_command_candidate(candidate)
            if resolved:
                return resolved
        return None

    def _runtime_command(self) -> str:
        return self.resolve_binary() or self.configured_command()

    def _candidate_commands(self) -> list[str]:
        configured = str(self.configured_command() or "").strip()
        candidates: list[str] = []
        if configured:
            candidates.append(configured)
        env_binary = str(os.environ.get("OPENCODE_BIN") or "").strip()
        if env_binary:
            candidates.append(env_binary)
        candidates.extend([
            str(Path.home() / ".opencode" / "bin" / "opencode"),
            str(Path.home() / ".local" / "bin" / "opencode"),
            "opencode",
        ])
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _resolve_command_candidate(candidate: str) -> str | None:
        raw = str(candidate or "").strip()
        if not raw:
            return None
        expanded = Path(raw).expanduser()
        if expanded.is_absolute() or os.sep in raw:
            return str(expanded) if expanded.is_file() and os.access(expanded, os.X_OK) else None
        return shutil.which(raw)

    async def is_available(self) -> bool:
        return self.resolve_binary() is not None

    async def get_status(self) -> AgentStatus:
        if self._process and self._process.returncode is None:
            return AgentStatus.RUNNING
        return AgentStatus.IDLE

    def supports_interactive(self) -> bool:
        return True

    def supports_session_resume(self) -> bool:
        return True

    def can_resume_without_session_id(self) -> bool:
        return True

    def agent_isolation_home_slug(self) -> str:
        # OpenCode discovers project/user config, agents, commands, plugins,
        # and skills from OPENCODE_CONFIG_DIR. Point spawned OpenCode at an
        # isolated config dir so the opc-collab skill is discoverable without
        # mutating the user's normal OpenCode config directory.
        return "opencode"

    def agent_home_env_vars(self, home: str) -> dict[str, str]:
        return {"OPENCODE_CONFIG_DIR": home}

    def post_install_agent_home(self, home: str) -> None:
        target_home = Path(home)
        target_home.mkdir(parents=True, exist_ok=True)
        source_home = self._user_config_dir(target_home)
        if source_home is None:
            return

        for file_name in ("opencode.json", "opencode.jsonc"):
            self._mirror_user_config_path(source_home / file_name, target_home / file_name)
        for dir_name in ("agent", "agents", "command", "commands", "plugin", "plugins"):
            self._mirror_user_config_path(source_home / dir_name, target_home / dir_name)

    @staticmethod
    def _user_config_dir(target_home: Path) -> Path | None:
        candidates: list[Path] = []
        raw_env = str(os.environ.get("OPENCODE_CONFIG_DIR") or "").strip()
        if raw_env:
            candidates.append(Path(raw_env).expanduser())
        xdg_config_home = str(os.environ.get("XDG_CONFIG_HOME") or "").strip()
        if xdg_config_home:
            candidates.append(Path(xdg_config_home).expanduser() / "opencode")
        candidates.append(Path.home() / ".config" / "opencode")

        target_resolved = target_home.expanduser().resolve()
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                continue
            if resolved == target_resolved:
                continue
            if resolved.exists():
                return resolved
        return None

    @staticmethod
    def _mirror_user_config_path(source: Path, target: Path) -> None:
        if not source.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            return
        try:
            target.symlink_to(source, target_is_directory=source.is_dir())
        except (OSError, NotImplementedError):
            try:
                if source.is_dir():
                    import shutil as _shutil

                    _shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    target.write_bytes(source.read_bytes())
            except OSError as exc:
                logger.warning(
                    "Unable to mirror OpenCode config path {} into isolated home: {}",
                    source,
                    exc,
                )

    def build_process_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str] | None:
        env = super().build_process_env(extra_env)
        config_dir = str((env or os.environ).get("OPENCODE_CONFIG_DIR") or "").strip()
        if config_dir:
            try:
                Path(config_dir).expanduser().mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("Unable to create OPENCODE_CONFIG_DIR {}: {}", config_dir, exc)
        if str(self.config.approval_mode or "auto").strip().lower() != "full-auto":
            return env

        merged = dict(os.environ if env is None else env)
        inline_config: dict[str, object] = {}
        raw_inline = str(merged.get("OPENCODE_CONFIG_CONTENT") or "").strip()
        if raw_inline:
            try:
                parsed = json.loads(raw_inline)
                if isinstance(parsed, dict):
                    inline_config = dict(parsed)
            except Exception:
                inline_config = {}
        inline_config["permission"] = "allow"
        merged["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            inline_config,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return merged

    def stdin_policy_for_process(
        self,
        cmd: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> ExternalAgentStdinPolicy:
        _ = metadata
        has_permission_handler = any(
            arg == "--permission-handler" or arg.startswith("--permission-handler=")
            for arg in cmd
        )
        return "pipe_open" if has_permission_handler else "devnull"

    def supports_approval_prompt_handling(
        self,
        cmd: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        mode = str(
            (metadata or {}).get("approval_mode")
            or self.config.approval_mode
            or "auto"
        ).strip().lower()
        if mode == "full-auto" or "--dangerously-skip-permissions" in cmd:
            return False
        return self.stdin_policy_for_process(cmd, metadata) == "pipe_open"

    def build_invocation(
        self,
        task: Task,
        workspace_path: str | None = None,
    ) -> tuple[list[str], dict[str, object]]:
        _ = workspace_path
        prompt = self.build_task_prompt(task)
        command = self._runtime_command()
        cmd = [
            command,
            "run",
            "--format",
            "default",
            *self._build_approval_args(),
            *self._build_thinking_args(),
            *self._build_session_args(),
            *self._build_model_args(),
            *list(self.config.extra_args),
            prompt,
        ]
        metadata = self.build_invocation_metadata(cmd)
        metadata["binary"] = command
        return cmd, metadata

    def build_interactive_invocation(
        self,
        task: Task,
        workspace_path: str | None = None,
    ) -> tuple[list[str], dict[str, object]]:
        _ = workspace_path
        prompt = self.build_task_prompt(task)
        command = self._runtime_command()
        cmd = [
            command,
            "run",
            "--format",
            "json",
            *self._build_approval_args(),
            *self._build_permission_handler_args(),
            *self._build_thinking_args(),
            *self._build_session_args(),
            *self._build_model_args(),
            *list(self.config.extra_args),
            prompt,
        ]
        metadata = self.build_invocation_metadata(cmd)
        metadata["binary"] = command
        return cmd, metadata

    def normalize_result_output(self, output: str) -> str:
        last_result = ""
        last_assistant = ""
        tool_summaries: list[str] = []
        saw_json_event = False
        for line in output.splitlines():
            event = self._parse_json_line(line)
            if not isinstance(event, dict):
                continue
            saw_json_event = True
            event_type = str(event.get("type") or event.get("event") or "").strip()
            if event_type in {"result", "session.result", "run.completed"}:
                text = self._event_text(event)
                if text:
                    last_result = text
            elif (
                "tool" in event_type
                or "command" in event_type
                or event_type.startswith("item.")
                or self._event_part_type(event) == "tool"
            ):
                summary = self._tool_summary(event)
                if summary:
                    tool_summaries.append(summary)
            elif (
                self._event_role(event) == "assistant"
                or event_type in {"assistant", "assistant_message", "message", "text"}
            ):
                text = self._event_text(event)
                if text:
                    last_assistant = text
        if last_result or last_assistant:
            return last_result or last_assistant
        if saw_json_event:
            return self._tool_only_result_fallback(tool_summaries)
        return output

    def format_progress_update(self, text: str, stream_name: str) -> str | None:
        if stream_name != "stdout":
            stripped = str(text or "").strip()
            return f"[External:{self.agent_type}:stderr] {stripped[:500]}" if stripped else None

        event = self._parse_json_line(text)
        if not isinstance(event, dict):
            return super().format_progress_update(text, stream_name)

        event_type = str(event.get("type") or event.get("event") or "").strip()
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        part_type = str(part.get("type") or "").strip()
        if event_type == "approval_request":
            return None
        if event_type in {"session", "session.started", "init", "step_start", "step-start"}:
            session_id = self._session_id_from_event(event)
            return (
                f"[External:{self.agent_type}:init] session={session_id[:8]}"
                if session_id
                else None
            )
        if (
            "tool" in event_type
            or "command" in event_type
            or event_type.startswith("item.")
            or part_type == "tool"
        ):
            summary = self._tool_summary(event)
            return f"[External:{self.agent_type}:tool] {summary}" if summary else None
        if event_type in {"thinking", "reasoning"} or part_type in {"thinking", "reasoning"}:
            message = self._event_text(event)
            return f"[External:{self.agent_type}:thinking] {message[:2400]}" if message else None
        if event_type in {"result", "session.result", "run.completed"}:
            result = self._event_text(event)
            return f"[External:{self.agent_type}:thinking] {result[:2400]}" if result else None
        if (
            self._event_role(event) == "assistant"
            or event_type in {"assistant", "assistant_message", "message", "text"}
        ):
            message = self._event_text(event)
            return f"[External:{self.agent_type}:thinking] {message[:2400]}" if message else None
        return None

    def detect_runtime_failure(self, text: str, stream_name: str) -> str | None:
        _ = stream_name
        lowered = str(text or "").lower()
        if "permission-handler" in lowered and ("unknown" in lowered or "invalid" in lowered):
            return "OpenCode rejected the configured stdio permission handler flag."
        return None

    def parse_approval_request(
        self,
        text: str,
        stream_name: str,
    ) -> ExternalApprovalRequest | None:
        event = self._parse_json_line(text)
        if not isinstance(event, dict) or str(event.get("type") or "").strip() != "approval_request":
            return super().parse_approval_request(text, stream_name)

        permission = event.get("permission")
        if not isinstance(permission, dict):
            return super().parse_approval_request(text, stream_name)

        permission_name = str(permission.get("permission") or "").strip()
        patterns = [str(item).strip() for item in permission.get("patterns") or [] if str(item).strip()]
        metadata = permission.get("metadata") if isinstance(permission.get("metadata"), dict) else {}
        common_metadata = {
            "stream": stream_name,
            "provider_event_type": "approval_request",
            "approval_id": str(permission.get("id") or "").strip(),
            "permission_name": permission_name,
            "permission_patterns": patterns,
            "permission_always": list(permission.get("always") or []),
            "provider_metadata": metadata,
            "raw_event": event,
        }

        normalized = self._normalize_name(permission_name)
        if normalized == "bash":
            command = self.normalize_shell_command(
                metadata.get("command") or metadata.get("cmd") or (patterns[0] if patterns else "")
            )
            arguments: dict[str, object] = {"command": command} if command else {}
            working_directory = (
                metadata.get("workdir") or metadata.get("cwd") or metadata.get("working_directory")
            )
            if working_directory:
                arguments["working_directory"] = str(working_directory)
            prompt_text = f"Allow OpenCode to run `{command}`?" if command else "Allow OpenCode to run a shell command?"
            return ExternalApprovalRequest(
                approval_scope="tool",
                action_name="shell_exec",
                prompt_text=prompt_text,
                arguments=arguments,
                metadata=common_metadata,
                raw_text=text,
            )

        path_value = (
            metadata.get("filepath")
            or metadata.get("filePath")
            or metadata.get("path")
            or (patterns[0] if patterns else "")
        )
        if normalized == "edit":
            arguments = {"path": str(path_value)} if path_value else {}
            prompt_text = (
                f"Allow OpenCode to edit `{path_value}`?" if path_value else "Allow OpenCode to edit files?"
            )
            return ExternalApprovalRequest(
                approval_scope="tool",
                action_name="file_edit",
                prompt_text=prompt_text,
                arguments=arguments,
                metadata=common_metadata,
                raw_text=text,
            )

        if normalized == "read":
            arguments = {"path": str(path_value)} if path_value else {}
            prompt_text = (
                f"Allow OpenCode to read `{path_value}`?" if path_value else "Allow OpenCode to read files?"
            )
            return ExternalApprovalRequest(
                approval_scope="tool",
                action_name="file_read",
                prompt_text=prompt_text,
                arguments=arguments,
                metadata=common_metadata,
                raw_text=text,
            )

        if normalized == "list":
            arguments = {"path": str(path_value)} if path_value else {}
            prompt_text = (
                f"Allow OpenCode to list `{path_value}`?" if path_value else "Allow OpenCode to list files?"
            )
            return ExternalApprovalRequest(
                approval_scope="tool",
                action_name="file_list",
                prompt_text=prompt_text,
                arguments=arguments,
                metadata=common_metadata,
                raw_text=text,
            )

        if normalized == "glob":
            arguments: dict[str, object] = {}
            if path_value:
                arguments["path"] = str(path_value)
            pattern = str(metadata.get("pattern") or (patterns[0] if patterns else "")).strip()
            if pattern:
                arguments["query"] = pattern
            prompt_text = f"Allow OpenCode to glob `{pattern}`?" if pattern else "Allow OpenCode to glob files?"
            return ExternalApprovalRequest(
                approval_scope="tool",
                action_name="file_glob",
                prompt_text=prompt_text,
                arguments=arguments,
                metadata=common_metadata,
                raw_text=text,
            )

        if normalized == "grep":
            arguments = {}
            if path_value:
                arguments["path"] = str(path_value)
            query = str(metadata.get("pattern") or (patterns[0] if patterns else "")).strip()
            if query:
                arguments["query"] = query
            prompt_text = f"Allow OpenCode to grep `{query}`?" if query else "Allow OpenCode to grep files?"
            return ExternalApprovalRequest(
                approval_scope="tool",
                action_name="file_search",
                prompt_text=prompt_text,
                arguments=arguments,
                metadata=common_metadata,
                raw_text=text,
            )

        if normalized == "externaldirectory":
            arguments = {"path": str(patterns[0])} if patterns else {}
            prompt_text = (
                f"Allow OpenCode to access `{patterns[0]}` outside the workspace?"
                if patterns
                else "Allow OpenCode to access directories outside the workspace?"
            )
            return ExternalApprovalRequest(
                approval_scope="external_agent",
                action_name=f"{self.agent_type}:external_directory",
                prompt_text=prompt_text,
                arguments=arguments,
                metadata=common_metadata,
                raw_text=text,
            )

        prompt_text = permission_name or "permission request"
        if patterns:
            prompt_text = f"{prompt_text}: {', '.join(patterns[:3])}"
        return ExternalApprovalRequest(
            approval_scope="external_agent",
            action_name=f"{self.agent_type}:{permission_name or 'permission'}",
            prompt_text=prompt_text,
            arguments={"target": patterns[0]} if patterns else {},
            metadata=common_metadata,
            raw_text=text,
        )

    def format_approval_response(
        self,
        request: ExternalApprovalRequest,
        approved: bool,
        decision: ApprovalDecision,
    ) -> str:
        approval_id = str(request.metadata.get("approval_id") or "").strip()
        if not approval_id:
            return super().format_approval_response(request, approved, decision)

        human_reply = str((decision.metadata or {}).get("human_reply") or "").strip().lower()
        reply = "reject"
        if approved:
            reply = "always" if human_reply in {"always_project", "always_global"} else "once"

        payload = {
            "type": "approval_response",
            "permission_id": approval_id,
            "reply": reply,
        }
        return json.dumps(payload, ensure_ascii=False) + "\n"

    async def plan_json(self, prompt: str, workspace_path: str) -> TaskResult:
        # The complete talent catalog can exceed OS argv limits. Give the
        # agent a file to read without truncating candidates or its request.
        request = Path(workspace_path) / "formation_recruitment_request.md"
        request.write_text(prompt, encoding="utf-8")
        return await super().plan_json(
            f"Read and follow the complete planning request in {request}. "
            "Return the complete JSON plan only. Do not execute the underlying task.",
            workspace_path,
        )

    async def execute(self, task: Task, workspace_path: str) -> TaskResult:
        if not await self.is_available():
            return TaskResult(status=TaskStatus.FAILED, content="OpenCode CLI not found")
        cmd, metadata = self.build_invocation(task, workspace_path=workspace_path)

        logger.info(f"OpenCode executing: {task.title}")

        try:
            stdin_policy = self.stdin_policy_for_process(cmd, metadata)
            self._record_stdin_policy_metadata(metadata, stdin_policy)
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=self._stdin_target_for_policy(stdin_policy),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path,
                **self._subprocess_group_kwargs(),
            )
            stdout, stderr = await asyncio.wait_for(self._process.communicate(), timeout=600)

            output = stdout.decode("utf-8", errors="replace")
            errors = stderr.decode("utf-8", errors="replace")

            if self._process.returncode == 0:
                return TaskResult(
                    status=TaskStatus.DONE,
                    content=output,
                    artifacts={**metadata, "stderr": errors} if errors else metadata,
                )
            return TaskResult(
                status=TaskStatus.FAILED,
                content=f"OpenCode exited with code {self._process.returncode}\n{errors}\n{output}",
                artifacts=metadata,
            )
        except asyncio.TimeoutError:
            if self._process:
                self._process.kill()
            return TaskResult(
                status=TaskStatus.FAILED,
                content="OpenCode timed out after 600s",
                artifacts=metadata,
            )
        except Exception as e:
            return TaskResult(
                status=TaskStatus.FAILED,
                content=f"OpenCode error: {e}",
                artifacts=metadata,
            )
        finally:
            self._process = None

    async def cancel(self, task_id: str) -> bool:
        if self._process and self._process.returncode is None:
            self._process.kill()
            return True
        return False

    def _build_model_args(self) -> list[str]:
        if not self.config.model:
            return []
        extra_args = list(self.config.extra_args)
        if self.config.model_flag and any(
            arg == self.config.model_flag or arg.startswith(f"{self.config.model_flag}=")
            for arg in extra_args
        ):
            return []
        flag = self.config.model_flag or "--model"
        return [flag, self.config.model]

    def _build_approval_args(self) -> list[str]:
        extra_args = list(self.config.extra_args)
        if any(arg == "--dangerously-skip-permissions" for arg in extra_args):
            return []
        mode = str(self.config.approval_mode or "auto").strip().lower()
        if mode == "full-auto":
            return ["--dangerously-skip-permissions"]
        return []

    def _build_permission_handler_args(self) -> list[str]:
        extra_args = list(self.config.extra_args)
        if any(
            arg == "--permission-handler" or arg.startswith("--permission-handler=")
            for arg in extra_args
        ):
            return []
        if self._supports_stdio_permission_handler():
            return ["--permission-handler", "stdio-json"]
        return []

    def _supports_stdio_permission_handler(self) -> bool:
        command = self.resolve_binary() or shutil.which(self.configured_command()) or self.configured_command()
        if not command:
            return False
        cached = self._permission_handler_support_cache.get(command)
        if cached is not None:
            return cached
        try:
            proc = subprocess.run(
                [command, "run", "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
                check=False,
            )
            help_text = f"{proc.stdout}\n{proc.stderr}"
            supported = "--permission-handler" in help_text
        except (OSError, subprocess.SubprocessError):
            supported = False
        self._permission_handler_support_cache[command] = supported
        return supported

    def _build_thinking_args(self) -> list[str]:
        extra_args = list(self.config.extra_args)
        if any(arg == "--thinking" for arg in extra_args):
            return []
        if bool(getattr(self.config, "show_thinking", False)):
            return ["--thinking"]
        return []

    def _build_session_args(self) -> list[str]:
        extra_args = list(self.config.extra_args)
        if any(arg in {"--continue", "--session"} or arg.startswith("--session=") for arg in extra_args):
            return []

        mode = str(self.config.session_mode or "auto").strip().lower()
        if mode == "resume":
            session_id = str(self.config.session_id or "").strip()
            if session_id:
                return ["--session", session_id]
            return ["--continue"]
        if mode == "new" and self.config.new_session_flag:
            return [self.config.new_session_flag]
        return []

    @classmethod
    def _session_id_from_event(cls, event: dict[str, Any]) -> str:
        for key in ("sessionID", "sessionId", "session_id", "id"):
            token = str(event.get(key) or "").strip()
            if token:
                return token
        for key in ("session", "message", "data", "result"):
            nested = event.get(key)
            if isinstance(nested, dict):
                token = cls._session_id_from_event(nested)
                if token:
                    return token
        return ""

    @staticmethod
    def _event_role(event: dict[str, Any]) -> str:
        role = str(event.get("role") or "").strip().lower()
        if role:
            return role
        message = event.get("message")
        if isinstance(message, dict):
            return str(message.get("role") or "").strip().lower()
        return ""

    @staticmethod
    def _event_part_type(event: dict[str, Any]) -> str:
        part = event.get("part")
        if isinstance(part, dict):
            return str(part.get("type") or "").strip().lower()
        return ""

    @classmethod
    def _event_text(cls, event: Any) -> str:
        if isinstance(event, str):
            return event.strip()
        if isinstance(event, list):
            parts = [cls._event_text(item) for item in event]
            return "\n".join(part for part in parts if part).strip()
        if not isinstance(event, dict):
            return ""

        for key in ("result", "message", "part", "text", "content", "summary", "output"):
            value = event.get(key)
            if key in {"message", "part"} and isinstance(value, dict):
                nested = cls._event_text(value)
                if nested:
                    return nested
            elif isinstance(value, (str, list, dict)):
                nested = cls._event_text(value)
                if nested:
                    return nested
        return ""

    @classmethod
    def _tool_summary(cls, event: dict[str, Any]) -> str:
        item = event.get("item") if isinstance(event.get("item"), dict) else event
        part = item.get("part") if isinstance(item.get("part"), dict) else {}
        if part:
            tool_name = str(part.get("tool") or part.get("name") or "tool").strip()
            normalized_tool = cls._normalize_name(tool_name)
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool_input = state.get("input") if isinstance(state.get("input"), dict) else {}
            command = cls.normalize_shell_command(
                tool_input.get("command") or tool_input.get("cmd") or tool_input.get("argv")
            )
            title = str(
                state.get("title")
                or tool_input.get("description")
                or state.get("description")
                or ""
            ).strip()
            metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
            raw_output = str(state.get("output") or metadata.get("output") or "").strip()
            status = str(state.get("status") or "").strip()
            lines: list[str] = []
            if command:
                lines.append(f"$ {command[:240]}")
            else:
                target = str(
                    tool_input.get("path")
                    or tool_input.get("file")
                    or tool_input.get("target")
                    or tool_input.get("pattern")
                    or tool_input.get("query")
                    or tool_input.get("searchTerm")
                    or ""
                ).strip()
                label = "web search" if normalized_tool in {"websearch", "web_search"} else tool_name
                lines.append(f"{label}: {target}".strip(": "))
            if title and title not in lines[0]:
                lines.append(title[:240])
            output = cls._summarize_tool_output(tool_name, raw_output)
            if output:
                lines.append(output)
            elif status:
                lines.append(f"status={status}")
            return "\n".join(line for line in lines if line).strip()

        source = item.get("tool_call") if isinstance(item.get("tool_call"), dict) else item
        name = str(
            source.get("name")
            or source.get("tool_name")
            or source.get("toolName")
            or source.get("type")
            or "tool"
        ).strip()
        command = cls.normalize_shell_command(source.get("command"))
        if command:
            return f"$ {command[:240]}"
        args = source.get("input") or source.get("arguments") or source.get("params") or {}
        if isinstance(args, dict):
            command = cls.normalize_shell_command(
                args.get("command") or args.get("cmd") or args.get("argv")
            )
            if command:
                return f"$ {command[:240]}"
            path = str(args.get("path") or args.get("file_path") or args.get("target") or "").strip()
            if path:
                return f"{name} {path}"
        output = str(source.get("aggregated_output") or source.get("output") or "").strip()
        if output and name != "tool":
            return f"{name}: {output[:200]}"
        return name

    @classmethod
    def _tool_only_result_fallback(cls, tool_summaries: list[str]) -> str:
        lines = [
            "OpenCode completed but did not emit a final assistant message.",
            "The raw JSON stream was parsed and suppressed from the user-facing reply.",
        ]
        cleaned = [cls._compact_multiline(item, limit=500) for item in tool_summaries if item]
        if cleaned:
            lines.append("")
            lines.append("Tool activity:")
            lines.extend(f"- {item}" for item in cleaned[-6:])
        return "\n".join(lines).strip()

    @classmethod
    def _summarize_tool_output(cls, tool_name: str, output: str) -> str:
        normalized_tool = cls._normalize_name(tool_name)
        if not output:
            return ""
        if normalized_tool in {"bash", "shell", "command", "exec"}:
            return output[:1200]
        parsed = cls._parse_json_line(output)
        if isinstance(parsed, dict):
            results = parsed.get("results")
            if isinstance(results, list):
                titles: list[str] = []
                for item in results[:5]:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or item.get("url") or "").strip()
                    if title:
                        titles.append(title)
                if titles:
                    return "results: " + "; ".join(titles)
            if "url" in parsed or "title" in parsed:
                return str(parsed.get("title") or parsed.get("url") or "").strip()[:500]
        return cls._compact_multiline(output, limit=500)

    @staticmethod
    def _compact_multiline(text: str, *, limit: int) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: max(0, limit - 1)].rstrip() + "…"
