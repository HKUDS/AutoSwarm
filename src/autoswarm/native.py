"""Owned native transports. No SDK dependency or shared orchestration service."""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import platform
from .approvals import Broker
from .records import SwarmError


@dataclass
class Output:
    session_id: str | None = None
    content: str = ""
    error: str | None = None
    terminal: bool = False
    permission_denials: list = field(default_factory=list)
    finish_reason: str | None = None
    cancelled: bool = False


class Native:
    def __init__(self, records, task, process, output, session_changed):
        self.task, self.process, self.output = task, process, output
        self.session_changed = session_changed
        self.broker = Broker(records, task)
        self.write_lock = threading.Lock()
        self.sequence = 0
        self.callbacks = {}
        self.endpoint = None
        self.stream = None
        self.work = None
        self.finished = threading.Event()

    def session(self, session_id):
        if session_id != self.output.session_id:
            self.output.session_id = session_id
            self.session_changed(session_id)

    def send(self, value):
        with self.write_lock:
            self.process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
            self.process.stdin.flush()

    def end_input(self):
        with self.write_lock:
            if not self.process.stdin.closed:
                self.process.stdin.close()

    def request(self, method, params, callback):
        self.sequence += 1
        self.callbacks[self.sequence] = callback
        self.send({"id": self.sequence, "method": method, "params": params})

    def start(self):
        if self.task["provider"] == "codex":
            def initialized(_):
                self.send({"method": "initialized", "params": {}})
                options = {"cwd": self.task["workspace"], **self.task.get("native_options", {})}
                if self.task.get("native_session_id"):
                    options["threadId"] = self.task["native_session_id"]
                self.request("thread/resume" if self.task.get("native_session_id") else "thread/start", options, started)

            def started(value):
                self.session(value["thread"]["id"])
                self.request("turn/start", {"threadId": self.output.session_id, "input": [{"type": "text", "text": self.task["prompt"]}]}, lambda _: None)

            self.request("initialize", {"clientInfo": {"name": "autoswarm-child", "version": "2.0.0.dev0"}}, initialized)
        elif self.task["provider"] == "claude-code":
            self.send({"type": "control_request", "request_id": "autoswarm_initialize", "request": {"subtype": "initialize"}})

    def feed(self, line):
        if self.task["provider"] == "opencode":
            match = re.search(r"opencode server listening on (http://127\.0\.0\.1:\d+)", line)
            if match and not self.endpoint:
                self.endpoint = match.group(1)
                self.work = threading.Thread(target=self.opencode, daemon=True)
                self.work.start()
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        if self.task["provider"] == "codex":
            self.codex(event)
        else:
            self.claude(event)

    def codex(self, event):
        method, params = event.get("method"), event.get("params", {})
        if not method and event.get("id") in self.callbacks:
            callback = self.callbacks.pop(event["id"])
            if "error" in event:
                raise SwarmError("native_rpc_error", str(event["error"]))
            callback(event.get("result", {}))
        elif method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/permissions/requestApproval"}:
            if params.get("threadId") != self.output.session_id:
                raise SwarmError("native_request_mismatch", "Permission request belongs to another native thread")
            def reply(value):
                allow = value["decision"] == "allow"
                if method == "item/permissions/requestApproval":
                    result = {"permissions": params.get("permissions", {}) if allow else {}, "scope": "session" if value["scope"] == "session" else "turn"}
                else:
                    rejection = "cancel" if params.get("availableDecisions") and "decline" not in params["availableDecisions"] else "decline"
                    result = {"decision": ("acceptForSession" if value["scope"] == "session" else "accept") if allow else rejection}
                self.send({"id": event["id"], "result": result})
            available = params.get("availableDecisions")
            scopes = ["once"]
            if method == "item/permissions/requestApproval" or not available or "acceptForSession" in available:
                scopes.append("session")
            self.broker.request(event["id"], self.output.session_id, {"method": method, **params}, reply, scopes=scopes)
        elif method == "serverRequest/resolved":
            self.broker.withdraw(params.get("requestId"), "resolved_by_native")
        elif method == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
            if params.get("threadId") == self.output.session_id:
                self.output.content = params["item"].get("text", "")
        elif method == "turn/completed":
            if params.get("threadId") != self.output.session_id:
                return
            turn = params.get("turn", {})
            self.output.terminal = True
            self.output.cancelled = turn.get("status") == "interrupted"
            self.output.error = None if turn.get("status") == "completed" else str(turn.get("error") or turn.get("status"))
            self.end_input()
        elif method and "id" in event:
            self.send({"id": event["id"], "error": {"code": -32601, "message": "Unsupported native interaction: " + method}})

    def claude(self, event):
        if event.get("type") == "control_response" and event.get("response", {}).get("request_id") == "autoswarm_initialize":
            if event["response"].get("subtype") == "error":
                raise SwarmError("native_control_error", str(event["response"]))
            self.send({"type": "user", "session_id": self.task.get("native_session_id") or "", "message": {"role": "user", "content": self.task["prompt"]}, "parent_tool_use_id": None})
        elif event.get("type") == "control_request":
            request = event.get("request", {})
            if request.get("subtype") != "can_use_tool":
                self.send({"type": "control_response", "response": {"subtype": "error", "request_id": event["request_id"], "error": "Unsupported native interaction"}})
                return
            def reply(value):
                response = {"behavior": value["decision"]}
                if value["decision"] == "allow":
                    response["updatedInput"] = request["input"]
                    if value["scope"] == "session":
                        response["updatedPermissions"] = [{**item, "destination": "session"} for item in request.get("permission_suggestions", []) if item.get("type") in {"addRules", "addDirectories"}]
                else:
                    response["message"] = value["message"] or "The approval was declined in the parent conversation"
                self.send({"type": "control_response", "response": {"subtype": "success", "request_id": event["request_id"], "response": response}})
            scopes = ["once"]
            if any(item.get("type") in {"addRules", "addDirectories"} for item in request.get("permission_suggestions", [])):
                scopes.append("session")
            self.broker.request(event["request_id"], self.output.session_id, request, reply, scopes=scopes)
        elif event.get("type") == "control_cancel_request":
            self.broker.withdraw(event.get("request_id"))
        else:
            self.session(event.get("session_id") or self.output.session_id)
            if event.get("type") == "result":
                self.output.terminal = True
                self.output.content = event.get("result", "")
                self.output.permission_denials = event.get("permission_denials", [])
                if event.get("is_error"):
                    error = event.get("errors") or self.output.content or event.get("subtype")
                    self.output.error = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
                self.end_input()
            elif event.get("type") == "assistant" and not self.output.terminal:
                blocks = event.get("message", {}).get("content", [])
                text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text")
                if text:
                    self.output.content = text

    def http(self, method, path, body=None):
        from .hosts import _http
        return _http({"endpoint": self.endpoint, "workspace": self.task["workspace"]}, method, path, body)

    def opencode_event(self, event):
        props = event.get("properties", {})
        if props.get("sessionID") != self.output.session_id:
            return
        if event.get("type") == "permission.asked":
            def reply(value):
                self.http("POST", "/permission/" + urllib.parse.quote(props["id"], safe="") + "/reply", {"reply": ("always" if value["scope"] == "session" else "once") if value["decision"] == "allow" else "reject", "message": value["message"]})
            self.broker.request(props["id"], self.output.session_id, props, reply, scopes=["once", "session"])
        elif event.get("type") == "permission.replied":
            self.broker.withdraw(props.get("requestID"), "resolved_by_native")

    def opencode(self):
        try:
            session = self.task.get("native_session_id")
            if not session:
                body = {"title": self.task.get("native_options", {}).get("title", "AutoSwarm child")}
                session = self.http("POST", "/session", body)["id"]
            else:
                self.http("GET", "/session/" + urllib.parse.quote(session, safe=""))
            self.session(session)
            endpoint = self.endpoint + "/event?directory=" + urllib.parse.quote(self.task["workspace"], safe="")
            # Use the same native authentication as ordinary API requests.
            from .hosts import http_headers
            self.stream = urllib.request.urlopen(urllib.request.Request(endpoint, headers=http_headers({})))

            def events():
                data = []
                try:
                    for raw in self.stream:
                        line = raw.decode("utf-8").rstrip("\r\n")
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        elif not line and data:
                            self.opencode_event(json.loads("\n".join(data)))
                            data = []
                    if not self.finished.is_set() and not self.broker.closed.is_set():
                        raise SwarmError("native_events_disconnected", "OpenCode permission event stream disconnected")
                except (OSError, ValueError, SwarmError) as error:
                    if not self.broker.closed.is_set() and not self.finished.is_set():
                        self.broker.errors.append(str(error))

            threading.Thread(target=events, daemon=True).start()
            body = {"parts": [{"type": "text", "text": self.task["prompt"]}]}
            for key, value in self.task.get("native_options", {}).items():
                if key == "model":
                    if "/" not in value:
                        raise SwarmError("invalid_model", "OpenCode models use provider/model syntax")
                    provider, model = value.split("/", 1)
                    body[key] = {"providerID": provider, "modelID": model}
                elif key in {"agent", "variant"}:
                    body[key] = value
            response = self.http("POST", "/session/" + urllib.parse.quote(session, safe="") + "/message", body)
            info = response.get("info", {})
            if info.get("sessionID") not in {None, session} or info.get("role") != "assistant":
                raise SwarmError("native_response_mismatch", "OpenCode did not return this session's assistant message")
            self.output.content = "\n".join(part.get("text", "") for part in response.get("parts", []) if part.get("type") == "text")
            self.output.finish_reason = info.get("finish")
            self.output.terminal = info.get("finish") == "stop"
            self.output.error = str(info["error"]) if info.get("error") else None
        except (OSError, ValueError, SwarmError) as error:
            self.output.error = str(error)
        finally:
            self.finished.set()
            platform.stop_child(self.process)

    def close(self):
        self.broker.close()
        if not self.process.stdin.closed:
            self.end_input()
