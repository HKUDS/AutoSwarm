"""One native ACP client shared by Cursor, Copilot and CodeBuddy."""
from . import platform
from .records import SwarmError


class ACP:
    def __init__(self, native):
        self.n = native
        self.accepting = False
        self.after_tool = False

    def start(self):
        self.n.stage("handshake")
        self.n.request("initialize", {"protocolVersion": 1, "clientInfo": {"name": "autoswarm", "version": "2.0.0.dev0"}, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}}, self.initialized)

    def initialized(self, result):
        if result.get("protocolVersion") != 1:
            raise SwarmError("native_protocol_error", "Unsupported ACP protocol version")
        self.n.progress("handshake", native_capabilities=result.get("agentCapabilities", {}))
        session = self.n.task.get("native_session_id")
        params = {"cwd": self.n.task["workspace"], "mcpServers": []}
        if session:
            if not result.get("agentCapabilities", {}).get("loadSession"):
                raise SwarmError("session_unavailable", "This native ACP server does not advertise session/load")
            params["sessionId"] = session
        self.n.stage("session")
        self.n.request("session/load" if session else "session/new", params, self.started)

    def started(self, result):
        self.n.session(result.get("sessionId") or self.n.task.get("native_session_id"))
        if not self.n.output.session_id:
            raise SwarmError("native_protocol_error", "ACP server returned no native session ID")
        self.n.output.content = ""  # session/load may replay the old transcript.
        self.accepting = True
        self.n.stage("execution")
        self.n.request("session/prompt", {"sessionId": self.n.output.session_id, "prompt": [{"type": "text", "text": self.n.task["prompt"]}]}, self.completed)

    def completed(self, result):
        reason = result.get("stopReason")
        self.n.output.finish_reason = reason
        self.n.output.terminal = reason == "end_turn"
        self.n.output.cancelled = reason == "cancelled"
        if not self.n.output.terminal and not self.n.output.cancelled:
            detail = result.get("_meta", {}).get("codebuddy.ai/errorMessage")
            self.n.output.error = "Native ACP prompt stopped: " + str(reason) + (": " + str(detail) if detail else "")
        self.n.end_input()
        platform.stop_child(self.n.process)

    def feed(self, event):
        method, params = event.get("method"), event.get("params", {})
        if method == "session/update":
            if params.get("sessionId") != self.n.output.session_id or not self.accepting:
                return
            update = params.get("update", {})
            if update.get("sessionUpdate") == "agent_message_chunk" and update.get("content", {}).get("type") == "text":
                if self.after_tool:
                    self.n.output.content = ""
                    self.after_tool = False
                self.n.output.content += update["content"].get("text", "")
            elif update.get("sessionUpdate") == "tool_call":
                self.after_tool = True
        elif method == "session/request_permission":
            if params.get("sessionId") != self.n.output.session_id:
                raise SwarmError("native_request_mismatch", "ACP permission belongs to another session")
            options = params.get("options", [])

            def reply(value):
                kind = "allow_once" if value["decision"] == "allow" else "reject_once"
                choice = next((item for item in options if item.get("kind") == kind), None)
                if choice:
                    outcome = {"outcome": "selected", "optionId": choice["optionId"]}
                elif value["decision"] == "deny":
                    outcome = {"outcome": "cancelled"}
                else:
                    raise SwarmError("unsupported_approval_scope", "Native ACP server offered no one-time approval; persistent grants require explicit native configuration")
                self.n.send({"id": event["id"], "result": {"outcome": outcome}})

            # ACP's allow_always may persist beyond this session. Never label
            # it as a session grant or silently broaden one-time permission.
            self.n.broker.request(event["id"], self.n.output.session_id, params, reply, scopes=["once"])
        elif "id" in event:
            self.n.send({"id": event["id"], "error": {"code": -32601, "message": "Unsupported native ACP interaction: " + str(method)}})
