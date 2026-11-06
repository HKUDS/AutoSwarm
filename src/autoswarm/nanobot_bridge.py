"""Local ACP wire shim, run by the user's installed nanobot Python.

No AutoSwarm imports or extra dependencies. Nanobot owns configuration, tools,
memory, permissions/sandbox, cancellation and persistent session histories.
"""
from __future__ import annotations

import os
import sys
# Executing this file must not shadow stdlib platform or installed nanobot
# with sibling AutoSwarm modules, including from an installed Skill copy.
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _script_dir]

import argparse
import asyncio
import contextlib
import io
import json
from pathlib import Path
import uuid


def load_native_config(args):
    from nanobot.config.loader import load_config, resolve_config_env_vars, set_config_path
    path = Path(args.config).expanduser().resolve() if args.config else None
    if path:
        if not path.is_file():
            raise ValueError("Native nanobot config does not exist: " + str(path))
        set_config_path(path)
    config = resolve_config_env_vars(load_config(path), config_path=path)
    if args.workspace:
        config.agents.defaults.workspace = str(Path(args.workspace).expanduser().resolve())
    return config


IDENTITY_KEYS = ("AUTOSWARM_CALL_ID", "AUTOSWARM_HOME", "PYTHONPATH")


def autoswarm_commands(config):
    """Let nanobot's commands use AutoSwarm the way other agents' commands do (this process only).

    Its shell passes commands a minimal environment, which would drop the call's identity: an
    agent started from here would not belong to this call. On Windows it runs each command in a
    job that ends the command's processes with it; a runner started with `run --background` may
    leave that job (only a process that asks to leave can), the others still end with it.
    """
    settings = getattr(getattr(config, "tools", None), "exec", None)
    if settings is not None and isinstance(getattr(settings, "allowed_env_keys", None), list):
        missing = [key for key in IDENTITY_KEYS if key in os.environ and key not in settings.allowed_env_keys]
        with contextlib.suppress(TypeError, ValueError, AttributeError):
            settings.allowed_env_keys = [*settings.allowed_env_keys, *missing]
    if os.name == "nt":
        with contextlib.suppress(ImportError):
            from nanobot.agent.tools import _windows_job
            if getattr(_windows_job, "_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE", None) == 0x2000:
                _windows_job._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000 | 0x800  # + JOB_OBJECT_LIMIT_BREAKAWAY_OK


class Bridge:
    def __init__(self, args, output):
        self.args, self.output = args, output
        self.bot = None
        self.session = None
        self.work = None
        self.stream = None

    def emit(self, value):
        self.output.write(json.dumps({"jsonrpc": "2.0", **value}, ensure_ascii=False) + "\n")
        self.output.flush()

    def update(self, update):
        self.emit({"method": "session/update", "params": {"sessionId": self.session, "update": update}})

    def create_bot(self):
        from nanobot.nanobot import Nanobot
        from nanobot.agent.loop import AgentLoop
        from nanobot.agent.hooks import create_file_edit_activity_hook
        from nanobot.agent.tools.mcp import MCPProvider
        from nanobot.agent.tools.registry import ToolRegistry

        config = load_native_config(self.args)
        if self.args.model:
            config.agents.defaults.model_preset = None
            config.agents.defaults.model = self.args.model
            config.agents.defaults.provider = "auto"  # Native SDK's explicit-model semantics.
        if self.args.effort:
            config.resolve_preset().reasoning_effort = self.args.effort
            if not config.agents.defaults.model_preset or config.agents.defaults.model_preset == "default":
                config.agents.defaults.reasoning_effort = self.args.effort
        autoswarm_commands(config)
        tools = ToolRegistry()
        extra = {}
        try:
            # nanobot 0.4 registers image generation as a tool before the loop is built.
            from nanobot.agent.tools.image_generation import register_image_generation_tool
        except ImportError:
            from nanobot.providers.image_generation import image_gen_provider_configs
            extra["image_generation_provider_configs"] = image_gen_provider_configs(config)
        else:
            register_image_generation_tool(config, tools, workspace=config.workspace_path)
        mcp = MCPProvider.from_config(config, tools)
        loop = AgentLoop.from_config(config, hook_factories=[create_file_edit_activity_hook], tool_registry=tools, **extra)
        # Same native SDK factory composition; only per-process config overrides.
        self.config = config
        return Nanobot(loop, config=config, mcp_provider=mcp)

    async def prompt(self, request):
        request_id = request["id"]
        try:
            text = "\n".join(v["text"] for v in request["params"]["prompt"] if v.get("type") == "text")
            if not text:
                raise ValueError("Provide a text prompt")
            self.stream = await self.bot.run_streamed(text, session_key=self.session, channel="websocket", chat_id=self.session.split(":", 1)[1])
            async for event in self.stream.stream_events():
                # What each tool is given and how it ended, as ACP agents report theirs (Desk shows the command).
                arguments = getattr(event, "arguments", None)
                if event.type == "tool.started":
                    self.update({"sessionUpdate": "tool_call", "toolCallId": str(event.tool_call_id), "title": event.name or "native tool",
                                 "status": "in_progress", **({"rawInput": arguments} if isinstance(arguments, dict) else {})})
                elif event.type in {"tool.completed", "tool.failed"}:
                    error = getattr(event, "error", None)
                    self.update({"sessionUpdate": "tool_call_update", "toolCallId": str(event.tool_call_id),
                                 "status": "completed" if event.type == "tool.completed" else "failed",
                                 **({"content": [{"type": "content", "content": {"type": "text", "text": str(error)}}]} if error else {})})
                elif event.type == "reasoning.delta" and getattr(event, "delta", ""):
                    self.update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": event.delta}})
            result = await self.stream.wait()
            if result.error:
                raise RuntimeError(result.error)
            if result.stop_reason == "cancelled":
                raise asyncio.CancelledError()
            if result.stop_reason not in {None, "completed"}:
                raise RuntimeError("Native nanobot run did not complete: " + str(result.stop_reason))
            self.update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": result.content}})
            self.emit({"id": request_id, "result": {"stopReason": "end_turn"}})
        except asyncio.CancelledError:
            self.emit({"id": request_id, "result": {"stopReason": "cancelled"}})
        except Exception as error:
            self.emit({"id": request_id, "error": {"code": -32603, "message": str(error)}})
        finally:
            self.stream = None

    async def handle(self, request):
        method, params = request.get("method"), request.get("params", {})
        if method == "initialize":
            result = {"protocolVersion": 1, "agentInfo": {"name": "autoswarm-nanobot-runtime"}, "agentCapabilities": {"loadSession": True}}
        elif method in {"session/new", "session/load"}:
            from nanobot.security.workspace_access import validate_workspace_scope_payload
            if self.session:
                raise ValueError("This native runtime already owns a session")
            if self.bot is None:
                self.bot = self.create_bot()
            key = params["sessionId"] if method == "session/load" else "websocket:autoswarm-" + uuid.uuid4().hex
            if method == "session/load" and self.bot.sessions.get(key) is None:
                raise ValueError("Native nanobot session does not exist: " + key)
            # No interactive approval channel: AutoSwarm defaults to native
            # Full Access, applied per session rather than to global config.
            scope = validate_workspace_scope_payload({"project_path": params["cwd"], "access_mode": self.args.access_mode},
                default_workspace=self.bot.runtime.workspace, default_restrict_to_workspace=self.config.tools.restrict_to_workspace)
            await self.bot.sessions.ingest(key, [], metadata={"workspace_scope": scope.metadata()})
            self.session = key
            result = {"sessionId": key}
            # The configured model, as ACP agents advertise theirs (AutoSwarm's model list reads it);
            # model and effort are chosen at launch (--model/--effort), never through a setter.
            model = getattr(self.config.resolve_preset(), "model", None) or self.config.agents.defaults.model
            if model:
                result["models"] = {"availableModels": [{"modelId": model, "name": model}], "currentModelId": model}
        elif method == "session/prompt":
            if params.get("sessionId") != self.session or self.bot is None:
                raise ValueError("Prompt belongs to another native session")
            if self.work and not self.work.done():
                raise ValueError("Native nanobot session is busy")
            self.work = asyncio.create_task(self.prompt(request))
            return
        elif method == "session/cancel":
            if params.get("sessionId") != self.session:
                raise ValueError("Cancel belongs to another native session")
            if self.stream:
                await self.stream.aclose()  # Native SDK owns cancellation and cleanup.
            if self.work and not self.work.done():
                self.work.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.work
            return
        else:
            if "id" in request:
                self.emit({"id": request["id"], "error": {"code": -32601, "message": "Unsupported nanobot runtime method: " + str(method)}})
            return
        self.emit({"id": request["id"], "result": result})

    async def run(self):
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                request = None
                try:
                    request = json.loads(line)
                    await self.handle(request)
                except Exception as error:
                    self.emit({"id": request.get("id") if isinstance(request, dict) else None,
                               "error": {"code": -32602, "message": str(error)}})
        finally:
            if self.stream:
                await self.stream.aclose()
            if self.work and not self.work.done():
                self.work.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.work
            if self.bot:
                await self.bot.aclose()


def private_wire():
    """Move the ACP wire to descriptors native tools do not inherit (dup'd ones are not, PEP 446).

    A tool's stdin becomes the null device and its stdout this process's stderr: its output can
    never corrupt the wire, and on Windows a child that inherited the pipe being read here could
    not even start until another line arrived.
    """
    wire_in, wire_out = os.dup(0), os.dup(1)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.dup2(2, 1)
    reader = io.TextIOWrapper(io.BufferedReader(io.FileIO(wire_in, "rb")), encoding="utf-8")
    writer = io.TextIOWrapper(io.BufferedWriter(io.FileIO(wire_out, "wb")), encoding="utf-8", newline="\n", line_buffering=True)
    return reader, writer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c")
    parser.add_argument("--workspace", "-w")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--access-mode", choices=("full", "restricted"), default="full")
    parser.add_argument("--skill-directory", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    sys.stdin, output = private_wire()
    sys.stdout = sys.stderr  # Native logging/tools must never corrupt the wire.
    if args.skill_directory:
        directory = Path(load_native_config(args).agents.defaults.workspace).expanduser().resolve() / "skills/autoswarm"
        output.write(json.dumps(str(directory), ensure_ascii=False) + "\n")
        return
    asyncio.run(Bridge(args, output).run())


if __name__ == "__main__":
    main()
