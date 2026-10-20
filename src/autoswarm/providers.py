"""Launch native agents with a bidirectional permission-capable transport."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field

from .records import SwarmError


ALIASES = {"claude": "claude-code", "claude_code": "claude-code"}
BINARIES = {"codex": "codex", "claude-code": "claude", "opencode": "opencode"}


def canonical(name: str) -> str:
    name = ALIASES.get(name, name)
    if name not in BINARIES:
        raise SwarmError("unknown_provider", f"Unknown provider: {name}")
    return name


def command_for(name: str, config: dict) -> list[str]:
    name = canonical(name)
    configured = config.get("providers", {}).get(name, {}).get("command", [BINARIES[name]])
    if not isinstance(configured, list) or not configured or not all(isinstance(x, str) for x in configured):
        raise SwarmError("invalid_config", "Provider command must be an argv array")
    binary = shutil.which(configured[0])
    if not binary:
        raise SwarmError("provider_unavailable", f"Executable not found: {configured[0]}")
    return [binary, *configured[1:]]


def invocation(task: dict, config: dict) -> list[str]:
    name = canonical(task["provider"])
    command = command_for(name, config)
    extras = []
    for extra in (config.get("providers", {}).get(name, {}).get("args", []), task.get("provider_args", [])):
        if not isinstance(extra, list) or not all(isinstance(value, str) for value in extra):
            raise SwarmError("invalid_config", "Provider args must be an array of strings")
        extras += extra
    options = {}
    native_args = []
    index = 0
    mappings = ({"--sandbox": "sandbox", "-s": "sandbox", "--ask-for-approval": "approvalPolicy", "-a": "approvalPolicy", "--model": "model", "-m": "model"}
                if name == "codex" else {"--model": "model", "-m": "model", "--agent": "agent", "--variant": "variant", "--title": "title"} if name == "opencode" else {})
    while index < len(extras):
        flag, sep, inline = extras[index].partition("=")
        if flag in mappings:
            if not sep and index + 1 >= len(extras):
                raise SwarmError("invalid_config", f"Missing native argument value: {flag}")
            options[mappings[flag]] = inline if sep else extras[index + 1]
            index += 1 if sep else 2
        elif name == "codex" and flag == "--full-auto":
            options.update({"sandbox": "workspace-write", "approvalPolicy": "on-request"})
            index += 1
        else:
            native_args.append(extras[index])
            index += 1
    if task.get("model"):
        options["model"] = task["model"]
    task["native_options"] = options
    task["protocol"] = {"codex": "codex-app-server", "claude-code": "claude-control", "opencode": "opencode-server"}[name]
    if name == "codex":
        command += ["app-server"]
    elif name == "claude-code":
        command += ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--permission-prompt-tool", "stdio"]
        if task.get("native_session_id"):
            command += ["--resume=" + task["native_session_id"]]
        if task.get("model"):
            command += ["--model", task["model"]]
    else:
        command += ["serve", "--hostname", "127.0.0.1", "--port", "0"]
    command += native_args
    return command


@dataclass
class Output:
    provider: str
    session_id: str | None = None
    content: str = ""
    error: str | None = None
    terminal: bool = False
    permission_denials: list = field(default_factory=list)
    text_parts: dict = field(default_factory=dict)
    finish_reason: str | None = None
    cancelled: bool = False

    def feed(self, line: str) -> None:
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if self.provider == "codex":
            if event.get("thread_id"):
                self.session_id = event["thread_id"]
            item = event.get("item", {})
            if kind == "item.completed" and item.get("type") == "agent_message":
                self.content = item.get("text", "")
            if kind == "turn.completed":
                self.terminal = True
                self.error = None
            if kind == "turn.failed":
                self.error = _error(event.get("error"))
                self.terminal = True
            if kind == "error":
                self.error = _error(event.get("message") or event.get("error"))
        elif self.provider == "claude-code":
            self.session_id = event.get("session_id") or self.session_id
            if kind == "result":
                self.terminal = True
                self.content = event.get("result", "")
                self.permission_denials = event.get("permission_denials", [])
                if event.get("is_error"):
                    self.error = _error(event.get("errors") or self.content or event.get("subtype"))
            elif kind == "assistant" and not self.terminal:
                blocks = event.get("message", {}).get("content", [])
                text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
                if text:
                    self.content = text
        else:
            self.session_id = event.get("sessionID") or event.get("session_id") or self.session_id
            part = event.get("part") or {}
            if kind == "text":
                self.text_parts[part.get("id", str(len(self.text_parts)))] = part.get("text", event.get("text", ""))
                self.content = "\n".join(self.text_parts.values())
            if kind == "step_start":
                self.terminal = False
                self.finish_reason = None
            if kind == "step_finish":
                self.finish_reason = part.get("reason")
                # Tool execution finishes a model step, not the delegated task.
                # Headless permission rejection can exit 0 after tool-calls.
                self.terminal = self.finish_reason == "stop"
            if kind == "error":
                self.error = _error(event.get("error") or event)


def _error(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("message") or value.get("data", {}).get("message") or json.dumps(value, ensure_ascii=False))
    return json.dumps(value, ensure_ascii=False)


def discover(config: dict) -> list[dict]:
    found = []
    for name in BINARIES:
        try:
            command = command_for(name, config)
            info = subprocess.run([*command, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
            found.append({"provider": name, "available": info.returncode == 0, "command": command, "version": info.stdout.strip(), "session_resume": True, "approval_relay": True})
        except (SwarmError, OSError) as error:
            found.append({"provider": name, "available": False, "error": str(error)})
    return found
