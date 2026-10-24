"""Controllable peers for the native bidirectional protocols (no model calls)."""
import json
import os
import queue
import re
import base64
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from http_peer import LocalHTTPServer

provider, *args = sys.argv[1:]
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
if "--version" in args:
    if "version-fail" in args:
        print("fixture CLI cannot start", file=sys.stderr)
        raise SystemExit(1)
    print("fixture 2.0")
    raise SystemExit()
session = "fixture-session"
trace_path = next((value.split("=", 1)[1] for value in args if value.startswith("--selection-trace=")), None)


def trace(value):
    if trace_path:
        with Path(trace_path).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")


trace({"argv": args, **({"config_dir": os.environ.get("CURSOR_CONFIG_DIR")} if provider == "cursor" else {})})


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def prepare(prompt):
    if prompt.startswith("gate:"):
        gate, prompt = prompt[5:].split("\n", 1)
        while not Path(gate).exists():
            time.sleep(0.01)
    if prompt.startswith("hold:"):
        Path(prompt[5:]).write_text("started")
        while True:
            time.sleep(0.01)
    return prompt


def finished(prompt):
    text = content(prompt)
    if provider == "codex":
        emit({"method": "item/completed", "params": {"threadId": session, "item": {"type": "agentMessage", "text": text}}})
        emit({"method": "turn/completed", "params": {"threadId": session, "turn": {"id": "native-turn", "status": "failed" if prompt == "fail" else "completed"}}})
    elif provider in {"cursor", "copilot", "codebuddy", "hermes", "nanobot", "dsh", "grok"}:
        if prompt == "with-tools":
            emit({"method": "session/update", "params": {"sessionId": session, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "INTERIM COMMENTARY"}}}})
            emit({"method": "session/update", "params": {"sessionId": session, "update": {"sessionUpdate": "tool_call", "toolCallId": "tool-1", "title": "fixture tool"}}})
        emit({"method": "session/update", "params": {"sessionId": session, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
        emit({"id": prompt_id, "result": {"stopReason": "refusal" if prompt == "fail" else "end_turn"}})
    elif provider == "muse":
        emit({"method": "item/completed", "params": {"sessionId": session, "item": {"kind": "agentMessage", "text": text}}})
        emit({"method": "turn/completed", "params": {"sessionId": session, "terminal": "failed" if prompt == "fail" else "completed"}})
    else:
        emit({"type": "result", "session_id": session, "result": text, "is_error": prompt == "fail", **({"errors": ["fixture failure"], "permission_denials": [{"tool_name": "Bash"}]} if prompt == "fail" else {})})


def content(prompt):
    memory = Path(".fixture-memory-" + provider)
    match = re.search(r"Remember the exact phrase (AUTOSWARM_\w+)", prompt)
    if match:
        memory.write_text(match.group(1), encoding="utf-8")
        return match.group(1)
    if prompt.startswith("What exact phrase"):
        return memory.read_text(encoding="utf-8")
    return prompt + "|session=" + session


if provider == "opencode":
    events = []
    decisions = {}
    permissions = {}
    health_requests = 0
    class HTTP(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def send(self, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            global health_requests
            path = self.path.split("?")[0]
            trace({"method": "GET", "path": path})
            if "auth-required" in args:
                expected = "Basic " + base64.b64encode((os.environ.get("OPENCODE_SERVER_USERNAME", "opencode") + ":" + os.environ.get("OPENCODE_SERVER_PASSWORD", "")).encode()).decode()
                if self.headers.get("Authorization") != expected:
                    self.send_error(401)
                    return
            if path == "/global/health":
                health_requests += 1
                if "slow-health" in args and health_requests == 1:
                    threading.Event().wait()
                if "reject-auth" in args:
                    self.send_error(401)
                    return
                if "bad-health" in args:
                    self.send_error(404)
                else:
                    self.send({"healthy": True, "version": "fixture"})
                return
            if path == "/doc":
                self.send({"paths": {} if "bad-protocol" in args else {"/session": {"post": {}}, "/session/{sessionID}/message": {"post": {}}, "/event": {"get": {}}, "/permission/{requestID}/reply": {"post": {}}}})
                return
            if path == "/provider":
                self.send({"all": [{"id": "fixture", "models": {"native-model": {"variants": {"deep-native": {}, "light-native": {}}}}}]})
                return
            if path == "/config":
                self.send({"model": "fixture/native-model"})
                return
            if path == "/agent":
                self.send([])
                return
            if path.endswith("/message"):
                self.send([])
                return
            if path == "/event":
                if "reject-events" in args:
                    self.send_error(401)
                    return
                channel = queue.Queue()
                events.append(channel)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"type":"server.connected"}\n\n')
                self.wfile.flush()
                while True:
                    value = channel.get()
                    self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
                    self.wfile.flush()
            elif path == "/permission":
                self.send(list(permissions.values()))
            else:
                self.send({"id": session})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            path = self.path.split("?")[0]
            trace({"method": "POST", "path": path, "params": body})
            if path == "/session":
                self.send({"id": session})
            elif path.startswith("/permission/"):
                request_id = path.split("/")[2]
                decisions[request_id][0] = body["reply"]
                decisions[request_id][1].set()
                self.send(True)
            elif path.endswith("/message"):
                prompt = prepare(body["parts"][0]["text"])
                if prompt.startswith("approval:"):
                    marker = prompt[9:]
                    request_id = "native-approval"
                    decisions[request_id] = [None, threading.Event()]
                    props = {"id": request_id, "sessionID": session, "permission": "bash", "patterns": ["test-command"], "always": ["test-command"], "metadata": {"command": "test-command", "marker": marker}}
                    permissions[request_id] = props
                    for channel in events:
                        channel.put({"type": "permission.asked", "properties": props})
                    decisions[request_id][1].wait()
                    allowed = decisions[request_id][0] != "reject"
                    if allowed:
                        Path(marker).write_text("executed", encoding="utf-8")
                    prompt = "approved" if allowed else "denied"
                self.send({"info": {"id": "native-assistant", "sessionID": session, "role": "assistant", "finish": "tool-calls" if prompt == "incomplete" else "stop", **({"error": {"message": "fixture failure"}} if prompt == "fail" else {})}, "parts": [{"type": "text", "text": content(prompt)}]})
        def log_message(self, *args):
            pass
    if "exit-startup" in args:
        print("fixture cannot bind server", file=sys.stderr)
        raise SystemExit(1)
    if "hold-startup" in args:
        while True:
            time.sleep(.1)
    server = LocalHTTPServer(("127.0.0.1", int(args[args.index("--port") + 1])), HTTP)
    server.daemon_threads = True
    print("Native service ready (changed banner)", file=sys.stderr, flush=True)
    server.serve_forever()
elif provider in {"cursor", "copilot", "codebuddy", "muse", "hermes", "nanobot", "dsh", "grok"}:
    marker = None
    prompt_id = None
    unsupported_replied = False
    for raw in sys.stdin:
        value = json.loads(raw)
        trace(value)
        method, params = value.get("method"), value.get("params", {})
        if method == "initialize":
            if "unknown-interaction" in args:
                emit({"id": "unsupported", "method": "future/interaction", "params": {}})
            result = {"schema": {"version": 2 if "bad-protocol" in args else 1}} if provider == "muse" else {"protocolVersion": 2 if "bad-protocol" in args else 1, "agentCapabilities": ({"sessionCapabilities": {"resume": {}}} if provider == "dsh" and "no-resume" not in args else {"loadSession": "no-resume" not in args})}
            emit({"id": value["id"], "result": result})
        elif method in {"session/start", "session/resume", "session/new", "session/load"}:
            if "auth-required" in args:
                emit({"id": value["id"], "error": {"code": -32000, "message": "Authentication required"}})
                continue
            session = params.get("sessionId", session)
            if method == "session/load":
                emit({"method": "session/update", "params": {"sessionId": session, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "OLD TRANSCRIPT MUST NOT LEAK"}}}})
            result = {"session": {"sessionId": session}} if provider == "muse" else {"sessionId": session}
            if "selection-catalog" in args and provider != "muse":
                result.update({"models": {"currentModelId": "native-model", "availableModels": [{"modelId": "native-model", "name": "Native Model"}]}, "configOptions": [{"id": "model", "category": "model", "options": [{"value": "native-model"}]}, {"id": "reasoning" if provider == "cursor" else "thought_level", "category": "thought_level", "options": [{"name": "Native choices", "options": [{"value": "deep-native"}, {"value": "light-native"}]}]}]})
            emit({"id": value["id"], "result": result})
        elif method in {"session/set_model", "session/set_config_option", "session/setModel", "session/setReasoningEffort"}:
            if "reject-selection" in args:
                emit({"id": value["id"], "error": {"code": -32602, "message": "Native model/effort selection was rejected"}})
            else:
                emit({"id": value["id"], "result": {"status": "accepted"} if provider == "muse" else {"configOptions": result.get("configOptions", [])}})
        elif method in {"turn/start", "session/prompt"}:
            if "unknown-interaction" in args:
                assert unsupported_replied, "unsupported native request was left unanswered"
            prompt_id = value["id"]
            if method == "turn/start":
                import uuid
                assert uuid.UUID(params["commandId"]).version == 7
                emit({"id": value["id"], "result": {}})
            prompt = prepare(params["input" if provider == "muse" else "prompt"][0]["text"])
            if prompt.startswith("approval:"):
                marker = prompt[9:]
                if provider == "muse":
                    emit({"id": "native-approval", "method": "approval/request", "params": {"sessionId": session, "approvalId": "native-approval", "currentRequirementId": {"approvalId": "native-approval", "sourceIndex": 0}, "availableChoices": [{"choiceId": "persist", "decision": "approved", "scope": "localPersistent"}, {"choiceId": "yes", "decision": "approved", "scope": "once"}, {"choiceId": "no", "decision": "abort" if "abort-only" in args else "denied", "scope": "once", "acceptsFeedback": True}], "command": "test-command", "marker": marker}})
                else:
                    emit({"id": "native-approval", "method": "session/request_permission", "params": {"sessionId": session, "toolCall": {"toolCallId": "tool-1", "title": "test-command", "kind": "execute", "rawInput": {"marker": marker}}, "options": [{"optionId": "yes", "kind": "allow_once", "name": "Allow once"}, {"optionId": "always", "kind": "allow_always", "name": "Allow always"}, {"optionId": "no", "kind": "reject_once", "name": "Reject once"}]}})
            else:
                finished(prompt)
        elif method == "approval/decide" or (value.get("id") == "native-approval" and "result" in value and provider != "muse"):
            allowed = params.get("choiceId") == "yes" if provider == "muse" else value["result"]["outcome"].get("optionId") == "yes"
            if method:
                emit({"id": value["id"], "result": {}})
            if allowed:
                Path(marker).write_text("executed", encoding="utf-8")
            finished("approved" if allowed else "denied")
        elif value.get("id") == "unsupported":
            unsupported_replied = value.get("error", {}).get("code") == -32601
else:
    pending_prompt = None
    marker = None
    for raw in sys.stdin:
        value = json.loads(raw)
        trace(value)
        if provider == "codex":
            method = value.get("method")
            if method == "initialize":
                emit({"id": value["id"], "result": {}})
            elif method in {"thread/start", "thread/resume"}:
                session = value["params"].get("threadId", session)
                emit({"id": value["id"], "result": {"thread": {"id": session}}})
            elif method == "turn/start":
                emit({"id": value["id"], "result": {"turn": {"id": "native-turn"}}})
                prompt = prepare(value["params"]["input"][0]["text"])
                if prompt.startswith("approval:"):
                    marker = prompt[9:]
                    pending_prompt = prompt
                    emit({"id": "native-approval", "method": "item/commandExecution/requestApproval", "params": {"threadId": session, "turnId": "native-turn", "itemId": "native-item", "command": "test-command", "cwd": os.getcwd()}})
                else:
                    finished(prompt)
            elif value.get("id") == "native-approval" and "result" in value:
                allowed = value["result"]["decision"] in {"accept", "acceptForSession"}
                if allowed:
                    Path(marker).write_text("executed", encoding="utf-8")
                finished("approved" if allowed else "denied")
        else:
            if value.get("type") == "control_request":
                emit({"type": "control_response", "response": {"subtype": "success", "request_id": value["request_id"], "response": {}}})
            elif value.get("type") == "user":
                session = value.get("session_id") or session
                emit({"type": "system", "subtype": "init", "session_id": session})
                prompt = prepare(value["message"]["content"])
                if prompt.startswith("approval:"):
                    marker = prompt[9:]
                    emit({"type": "control_request", "request_id": "native-approval", "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "test-command", "marker": marker}, "tool_use_id": "native-tool"}})
                elif prompt.startswith("question:"):
                    marker = prompt[len("question:"):]
                    emit({"type": "control_request", "request_id": "native-question", "request": {"subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": [{"question": "Choose scope?", "options": [{"label": "本地"}, {"label": "Shared"}], "multiSelect": False}]}}})
                else:
                    finished(prompt)
            elif value.get("type") == "control_response" and value.get("response", {}).get("request_id") == "native-question":
                response = value["response"]["response"]
                assert response["behavior"] == "allow"
                assert response["updatedInput"]["answers"] == {"Choose scope?": "本地"}
                Path(marker).write_text("answered", encoding="utf-8")
                finished("answered")
            elif value.get("type") == "control_response":
                allowed = value["response"]["response"]["behavior"] == "allow"
                if allowed:
                    Path(marker).write_text("executed", encoding="utf-8")
                finished("approved" if allowed else "denied")
