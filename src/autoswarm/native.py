"""Owned native transports. No SDK dependency or shared orchestration service."""
from __future__ import annotations

import json
import errno
import threading
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field

from .approvals import Broker
from .records import SwarmError
from .providers import ACP_PROVIDERS


@dataclass
class Output:
    session_id: str | None = None
    content: str = ""
    error: str | None = None
    error_code: str | None = None
    terminal: bool = False
    permission_denials: list = field(default_factory=list)
    finish_reason: str | None = None
    cancelled: bool = False


class Native:
    def __init__(self, records, task, process, output, session_changed, progress=lambda phase, **details: None):
        self.task, self.process, self.output = task, process, output
        self.session_changed = session_changed
        self.progress = progress
        self.phase = "startup"
        self.broker = Broker(records, task)
        self.write_lock = threading.Lock()
        self.request_lock = threading.Lock()
        self.sequence = 0
        self.callbacks = {}
        self.rpc_errors = {}
        self.turn_done = threading.Event()
        self.turn_id = None
        self.ready = False
        self.control_errors = {}
        self.endpoint = task.get("native_endpoint")
        self.stream = None
        self.work = None
        self.finished = threading.Event()
        self.adapter = None
        self.initial_messages = []
        if task["provider"] in ACP_PROVIDERS:
            from .acp import ACP
            self.adapter = ACP(self)
        elif task["provider"] == "muse":
            from .muse import Muse
            self.adapter = Muse(self)

    def session(self, session_id):
        if session_id != self.output.session_id:
            self.output.session_id = session_id
            self.session_changed(session_id)

    def send(self, value):
        if self.adapter:
            value = {"jsonrpc": "2.0", **value}
        with self.write_lock:
            self.process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
            self.process.stdin.flush()

    def end_input(self):
        with self.write_lock:
            if not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass  # An exited native peer must not hide saved results.

    def request(self, method, params, callback, on_error=None):
        with self.request_lock:
            self.sequence += 1
            request_id = self.sequence
            self.callbacks[request_id] = callback
            if on_error:
                self.rpc_errors[request_id] = on_error
        self.send({"id": request_id, "method": method, "params": params})

    def rpc_response(self, event):
        callback = self.callbacks.pop(event["id"])
        failure = self.rpc_errors.pop(event["id"], None)
        if "error" in event:
            error = event["error"]
            if failure:
                failure(error)
                return
            code = "native_auth_required" if error.get("code") == -32000 and ("auth" in error.get("message", "").lower() or (error.get("data") or {}).get("category") == "auth") else "native_rpc_error"
            raise SwarmError(code, json.dumps(error, ensure_ascii=False))
        callback(event.get("result", {}))

    def initial_prompt(self):
        from .messages import context, envelope, pending, receipt
        text = self.task["prompt"]
        if self.task.get("communication") and not self.task.get("message_only"):
            text += context(self.task)
        if self.task.get("message_only"):
            values = pending(self.broker.records, self.task["agent_id"])
            self.initial_messages = values
            text += envelope(self.task, values)
            for value in values:
                receipt(self.broker.records, value, "submitted", mechanism="native_resume", call_id=self.task["call_id"])
        return text

    def continue_turn(self, text):
        for key, value in vars(Output(session_id=self.output.session_id)).items():
            setattr(self.output, key, value)
        self.turn_done.clear()
        self.turn_id = None
        self.ready = True
        if self.adapter:
            self.adapter.prompt(text)
        elif self.task["provider"] == "codex":
            self.request("turn/start", {"threadId": self.output.session_id, "input": [{"type": "text", "text": text}]}, lambda value: setattr(self, "turn_id", value.get("turn", {}).get("id")))
        elif self.task["provider"] == "claude-code":
            self.send({"type": "user", "session_id": self.output.session_id or "", "message": {"role": "user", "content": text}, "parent_tool_use_id": None})
        else:
            self.work = threading.Thread(target=self.opencode_prompt, args=(text,), daemon=True)
            self.work.start()

    def insert(self, text, accepted, rejected):
        name = self.task["provider"]
        if name == "codex" and self.turn_id:
            self.request("turn/steer", {"threadId": self.output.session_id, "expectedTurnId": self.turn_id, "input": [{"type": "text", "text": text}]}, accepted, rejected)
            return True
        if name == "muse" and self.turn_id:
            from .muse import command_id
            self.request("turn/steer", {"commandId": command_id(), "sessionId": self.output.session_id, "expectedTurnId": self.turn_id, "input": [{"type": "text", "text": text}]}, accepted, rejected)
            return True
        if name == "opencode":
            def submit():
                try:
                    accepted(self.http("POST", "/session/" + urllib.parse.quote(self.output.session_id, safe="") + "/message", {"noReply": True, "parts": [{"type": "text", "text": text}]}))
                except Exception as error:
                    rejected(error)
            threading.Thread(target=submit, daemon=True).start()
            return True
        # ACP v1 and Claude's queued user input have no common live-steer
        # acknowledgement. Their ordinary mail is delivered at a turn boundary.
        return False

    def interrupt(self, rejected):
        name = self.task["provider"]
        if name == "codex" and self.turn_id:
            self.request("turn/interrupt", {"threadId": self.output.session_id, "turnId": self.turn_id}, lambda _: None, rejected)
        elif name == "muse":
            from .muse import command_id
            params = {"sessionId": self.output.session_id, "commandId": command_id()}
            if self.turn_id:
                params["turnId"] = self.turn_id
            self.request("turn/interrupt", params, lambda _: None, rejected)
        elif self.adapter:
            self.adapter.interrupt()
        elif name == "claude-code":
            key = "autoswarm_interrupt_" + str(self.sequence)
            self.sequence += 1
            self.control_errors[key] = rejected
            self.send({"type": "control_request", "request_id": key, "request": {"subtype": "interrupt"}})
        elif name == "opencode":
            def stop():
                try:
                    self.http("POST", "/session/" + urllib.parse.quote(self.output.session_id, safe="") + "/abort", {})
                except Exception as error:
                    rejected(error)
            threading.Thread(target=stop, daemon=True).start()
        else:
            rejected("This native transport did not expose a running turn to interrupt")

    def finish_turn(self):
        # A completed/cancelled native turn cannot accept a late permission
        # answer. Keep its record, and let the next turn ask afresh if needed.
        for path in (self.broker.directory / "approvals").glob("approval_*"):
            from .records import read_json
            request = read_json(path / "request.json")
            if request:
                self.broker.withdraw(request["native_request_id"], "cancelled" if (self.broker.directory / "cancel.json").exists() else "native_turn_ended")
        self.turn_done.set()

    def start(self):
        if self.adapter:
            self.adapter.start()
        elif self.task["provider"] == "codex":
            def initialized(_):
                self.send({"method": "initialized", "params": {}})
                options = {"cwd": self.task["workspace"], **self.task.get("native_options", {})}
                if self.task.get("native_session_id"):
                    options["threadId"] = self.task["native_session_id"]
                self.request("thread/resume" if self.task.get("native_session_id") else "thread/start", options, started)

            def started(value):
                self.session(value["thread"]["id"])
                self.continue_turn(self.initial_prompt())

            self.request("initialize", {"clientInfo": {"name": "autoswarm-child", "version": "2.0.0.dev0"}}, initialized)
        elif self.task["provider"] == "claude-code":
            self.send({"type": "control_request", "request_id": "autoswarm_initialize", "request": {"subtype": "initialize"}})
        else:
            self.work = threading.Thread(target=self.opencode, daemon=True)
            self.work.start()

    def feed(self, line):
        if self.task["provider"] == "opencode":
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        if self.adapter:
            if not event.get("method") and event.get("id") in self.callbacks:
                self.rpc_response(event)
            else:
                self.adapter.feed(event)
            return
        if self.task["provider"] == "codex":
            self.codex(event)
        else:
            self.claude(event)

    def codex(self, event):
        method, params = event.get("method"), event.get("params", {})
        if not method and event.get("id") in self.callbacks:
            self.rpc_response(event)
        elif method == "turn/started" and params.get("threadId") == self.output.session_id:
            self.turn_id = params.get("turn", {}).get("id")
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
            self.finish_turn()
        elif method and "id" in event:
            self.send({"id": event["id"], "error": {"code": -32601, "message": "Unsupported native interaction: " + method}})

    def claude(self, event):
        if event.get("type") == "control_response" and event.get("response", {}).get("request_id") == "autoswarm_initialize":
            if event["response"].get("subtype") == "error":
                raise SwarmError("native_control_error", str(event["response"]))
            self.continue_turn(self.initial_prompt())
        elif event.get("type") == "control_response" and event.get("response", {}).get("request_id") in self.control_errors:
            callback = self.control_errors.pop(event["response"]["request_id"])
            if event["response"].get("subtype") == "error":
                callback(event["response"])
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
                self.finish_turn()
            elif event.get("type") == "assistant" and not self.output.terminal:
                blocks = event.get("message", {}).get("content", [])
                text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text")
                if text:
                    self.output.content = text

    def http(self, method, path, body=None, *, timeout=None, stream=False):
        from .hosts import _http, open_http
        try:
            parent = {"endpoint": self.endpoint}
            if path not in {"/global/health", "/doc"}:
                parent["workspace"] = self.task["workspace"]
            self.progress(self.phase, http_method=method, http_path=path)
            return (open_http if stream else _http)(parent, method, path, body, timeout=timeout)
        except urllib.error.HTTPError as error:
            code = "native_server_auth_failed" if error.code in {401, 403} else "native_protocol_error" if error.code in {404, 405} else "native_http_error"
            evidence = error.read().decode("utf-8", errors="replace")
            raise SwarmError(code, f"OpenCode {self.phase}: {method} {path} returned HTTP {error.code}: {evidence}") from error

    def stage(self, phase, **details):
        self.phase = phase
        self.progress(phase, **details)

    def handshake(self):
        self.stage("startup", endpoint=self.endpoint)
        while self.process.poll() is None and not self.broker.closed.is_set():
            try:
                # One readiness poll must yield even if a cold native server
                # accepts a socket before it can answer. Only this idempotent
                # GET is retried; there is no total startup or task deadline.
                health = self.http("GET", "/global/health", timeout=0.5)
                if not isinstance(health, dict) or health.get("healthy") is not True:
                    raise SwarmError("native_protocol_error", "OpenCode health endpoint did not return healthy=true; inspect stdout.log and stderr.log")
                break
            except (urllib.error.URLError, TimeoutError) as error:
                reason = getattr(error, "reason", error)
                if not isinstance(reason, TimeoutError) and getattr(reason, "errno", None) not in {errno.ECONNREFUSED, 10061}:
                    raise
                self.progress("startup", endpoint=self.endpoint, diagnostic=str(reason))
                self.broker.closed.wait(0.1)
        else:
            raise SwarmError("native_start_failed", "OpenCode exited before its HTTP service was ready; inspect stdout.log and stderr.log")
        self.stage("handshake", native_version=health.get("version"), diagnostic=None)
        spec = self.http("GET", "/doc")
        paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
        required = {"/session": "post", "/session/{sessionID}/message": "post", "/event": "get", "/permission/{requestID}/reply": "post"}
        missing = [path for path, method in required.items() if method not in paths.get(path, {})]
        if missing:
            raise SwarmError("native_protocol_error", "OpenCode's advertised API does not support this adapter: " + ", ".join(missing) + ". Check the actual CLI version and update AutoSwarm; do not reinstall authentication.")

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
            self.handshake()
            self.stage("session")
            session = self.task.get("native_session_id")
            if not session:
                body = {"title": self.task.get("native_options", {}).get("title", "AutoSwarm child")}
                session = self.http("POST", "/session", body)["id"]
            else:
                self.http("GET", "/session/" + urllib.parse.quote(session, safe=""))
            self.session(session)
            self.stream = self.http("GET", "/event", stream=True)
            if "text/event-stream" not in self.stream.headers.get("Content-Type", ""):
                raise SwarmError("native_protocol_error", "OpenCode /event did not return an SSE stream")

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
                except Exception as error:
                    if not self.broker.closed.is_set() and not self.finished.is_set():
                        self.broker.errors.append(str(error))

            threading.Thread(target=events, daemon=True).start()
            self.stage("execution")
            self.ready = True
            self.opencode_prompt(self.initial_prompt())
        except Exception as error:
            self.output.error = str(error)
            self.output.error_code = getattr(error, "code", "native_connection_failed" if isinstance(error, OSError) else "native_protocol_error")
            self.finish_turn()

    def opencode_prompt(self, text):
        try:
            session = self.output.session_id
            body = {"parts": [{"type": "text", "text": text}]}
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
        except Exception as error:
            self.output.error = str(error)
            self.output.error_code = getattr(error, "code", "native_connection_failed" if isinstance(error, OSError) else "native_protocol_error")
        finally:
            self.finish_turn()

    def close(self):
        self.finished.set()
        self.broker.close()
        if not self.process.stdin.closed:
            self.end_input()
