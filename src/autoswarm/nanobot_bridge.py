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
        from nanobot.providers.image_generation import image_gen_provider_configs

        config = load_native_config(self.args)
        if self.args.model:
            config.agents.defaults.model_preset = None
            config.agents.defaults.model = self.args.model
            config.agents.defaults.provider = "auto"  # Native SDK's explicit-model semantics.
        if self.args.effort:
            config.resolve_preset().reasoning_effort = self.args.effort
            if not config.agents.defaults.model_preset or config.agents.defaults.model_preset == "default":
                config.agents.defaults.reasoning_effort = self.args.effort
        tools = ToolRegistry()
        mcp = MCPProvider.from_config(config, tools)
        loop = AgentLoop.from_config(config, image_generation_provider_configs=image_gen_provider_configs(config),
                                     hook_factories=[create_file_edit_activity_hook], tool_registry=tools)
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
                if event.type == "tool.started":
                    self.update({"sessionUpdate": "tool_call", "toolCallId": str(event.tool_call_id), "title": event.name or "native tool"})
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c")
    parser.add_argument("--workspace", "-w")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--access-mode", choices=("full", "restricted"), default="full")
    parser.add_argument("--skill-directory", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = sys.stdout
    output.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout = sys.stderr  # Native logging/tools must never corrupt the wire.
    if args.skill_directory:
        directory = Path(load_native_config(args).agents.defaults.workspace).expanduser().resolve() / "skills/autoswarm"
        output.write(json.dumps(str(directory), ensure_ascii=False) + "\n")
        return
    asyncio.run(Bridge(args, output).run())


if __name__ == "__main__":
    main()
