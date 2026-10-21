"""Responsive native peers for communication lifecycle acceptance tests."""
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from autoswarm import messages
from autoswarm.records import Records, read_json, write_json

provider, *args = sys.argv[1:]
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
records = Records()
task = records.require(os.environ["AUTOSWARM_CALL_ID"])[1]
agent_id = task["agent_id"]
session = task.get("native_session_id") or "conversation-" + agent_id
memory = Path(".conversation-" + session + ".json")
guard = threading.Lock()
stop = threading.Event()
work_thread = None
turn = None
permission = None
permission_event = threading.Event()
permission_allowed = False
cancel_barrier = False
prompt_count = 0
selection = {}


def emit(value):
    with guard:
        print(json.dumps(value, ensure_ascii=False), flush=True)


def log(kind):
    with Path("events-" + agent_id).open("a", encoding="utf-8") as stream:
        stream.write(kind + "\n")


def completed(request, cancelled=False, text="assignment complete"):
    global cancel_barrier
    cancel_barrier = cancelled
    if provider == "codex":
        emit({"method": "item/completed", "params": {"threadId": session, "item": {"type": "agentMessage", "text": text}}})
        emit({"method": "turn/completed", "params": {"threadId": session, "turn": {"id": turn, "status": "interrupted" if cancelled else "completed"}}})
    elif provider == "muse":
        emit({"method": "item/completed", "params": {"sessionId": session, "item": {"kind": "agentMessage", "text": text}}})
        emit({"method": "turn/completed", "params": {"sessionId": session, "turnId": turn, "terminal": "cancelled" if cancelled else "completed"}})
    elif provider == "claude-code":
        emit({"type": "result", "session_id": session, "result": text, "is_error": False})
    elif provider != "opencode":
        emit({"method": "session/update", "params": {"sessionId": session, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
        emit({"id": request, "result": {"stopReason": "cancelled" if cancelled else "end_turn", "userMessageId": "accepted-user", "_meta": {"codebuddy.ai/requestId": "previous-request"}}})


def mail(text):
    marker = "Incoming messages (preserve sender identity; answer questions using reply):\n"
    if marker not in text:
        return
    values, _ = json.JSONDecoder().raw_decode(text.split(marker, 1)[1])
    for value in values:
        if value["kind"] != "agent_message" or value.get("reply_to"):
            continue
        messages.reply(records, value["message_id"], "reply:" + value["content"])
        log("replied:" + value["message_id"])


def work(text, request, done=None):
    global permission, permission_allowed
    try:
        if text.startswith("{"):
            assignment, _ = json.JSONDecoder().raw_decode(text)
            write_json(memory, assignment)
        assignment = read_json(memory, {})
        log("started")
        mail(text)
        if assignment.get("approval") and not Path(assignment["proof"]).exists():
            permission = "permission-" + uuid.uuid4().hex
            permission_allowed = False
            permission_event.clear()
            emit({"id": permission, "method": "session/request_permission", "params": {"sessionId": session, "toolCall": {"toolCallId": permission, "title": "write fixture proof"}, "options": [{"optionId": "yes", "kind": "allow_once", "name": "Allow"}, {"optionId": "no", "kind": "reject_once", "name": "Deny"}]}})
            while not permission_event.wait(.01) and not stop.is_set():
                pass
        gate = assignment.get("gate")
        while gate and not Path(gate).exists() and not stop.is_set():
            time.sleep(.01)
        if stop.is_set():
            log("cancelled")
            completed(request, True)
        else:
            proof = assignment.get("proof")
            if proof and (not assignment.get("approval") or permission_allowed):
                Path(proof).write_text("finished original assignment", encoding="utf-8")
            completed(request)
    finally:
        if done:
            done.set()


def start(text, request=None, done=None):
    global stop, work_thread, turn
    stop = threading.Event()
    turn = "turn-" + uuid.uuid4().hex
    work_thread = threading.Thread(target=work, args=(text, request, done), daemon=True)
    work_thread.start()


if provider == "opencode":
    class HTTP(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def send(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/global/health":
                self.send({"healthy": True})
            elif path == "/doc":
                self.send({"paths": {"/session": {"post": {}}, "/session/{sessionID}/message": {"post": {}}, "/event": {"get": {}}, "/permission/{requestID}/reply": {"post": {}}}})
            elif path == "/event":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"type":"server.connected"}\n\n')
                self.wfile.flush()
                threading.Event().wait()
            else:
                self.send({"id": session})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            path = self.path.split("?")[0]
            if path == "/session":
                self.send({"id": session})
            elif path.endswith("/abort"):
                stop.set()
                self.send(True)
            elif path.endswith("/message") and body.get("noReply"):
                mail(body["parts"][0]["text"])
                self.send({"info": {"role": "user"}})
            elif path.endswith("/message"):
                done = threading.Event()
                start(body["parts"][0]["text"], done=done)
                done.wait()
                self.send({"info": {"role": "assistant", "sessionID": session, "finish": "stop"}, "parts": [{"type": "text", "text": "assignment complete"}]})
        def log_message(self, *args):
            pass
    ThreadingHTTPServer(("127.0.0.1", int(args[args.index("--port") + 1])), HTTP).serve_forever()
else:
    for raw in sys.stdin:
        event = json.loads(raw)
        method, params = event.get("method"), event.get("params", {})
        if method == "initialize":
            result = {"schema": {"version": 1}} if provider == "muse" else {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
            emit({"id": event["id"], "result": result})
        elif method in {"thread/start", "thread/resume", "session/start", "session/resume", "session/new", "session/load"}:
            result = {"thread": {"id": session}} if provider == "codex" else {"session": {"sessionId": session}} if provider == "muse" else {"sessionId": session}
            if provider in {"cursor", "copilot", "codebuddy", "hermes", "nanobot", "dsh", "grok"}:
                result["configOptions"] = [{"id": "thought_level", "category": "thought_level", "options": [{"value": "native-effort"}]}]
            emit({"id": event["id"], "result": result})
        elif method == "session/set_model":
            selection["model"] = params["modelId"]
            emit({"id": event["id"], "result": {}})
        elif method == "session/set_config_option":
            selection["effort" if params["configId"] == "thought_level" else params["configId"]] = params["value"]
            emit({"id": event["id"], "result": {}})
        elif method in {"turn/start", "session/prompt"}:
            if provider == "codex" and any(key in params for key in ("model", "effort")):
                log("selection:" + json.dumps({key: params[key] for key in ("model", "effort") if key in params}, sort_keys=True))
            elif selection:
                log("selection:" + json.dumps(selection, sort_keys=True))
            prompt_count += 1
            if "reject-prompt" in args or ("reject-followup" in args and prompt_count > 1):
                emit({"id": event["id"], "error": {"code": -1, "message": "fixture prompt rejected before acceptance"}})
                continue
            if provider == "codebuddy" and cancel_barrier:
                cancel_barrier = False
                emit({"id": event["id"], "result": {"stopReason": "cancelled", "_meta": {"codebuddy.ai/requestId": "previous-request"}}})
                continue
            if method == "turn/start":
                # Native acknowledgements precede completion, including for a fast reply.
                turn = "turn-" + uuid.uuid4().hex
                emit({"id": event["id"], "result": {"turn": {"id": turn}, "turnId": turn}})
            start(params["input" if method == "turn/start" else "prompt"][0]["text"], event["id"])
        elif method == "turn/steer":
            if "reject-insert" in args:
                emit({"id": event["id"], "error": {"code": -1, "message": "fixture live input rejected"}})
            else:
                emit({"id": event["id"], "result": {"turnId": turn}})
                threading.Thread(target=mail, args=(params["input"][0]["text"],), daemon=True).start()
        elif method in {"turn/interrupt", "session/cancel"}:
            stop.set()
            if "id" in event:
                emit({"id": event["id"], "result": {}})
        elif event.get("type") == "control_request":
            if event["request"]["subtype"] == "interrupt":
                stop.set()
            emit({"type": "control_response", "response": {"subtype": "success", "request_id": event["request_id"], "response": {}}})
        elif event.get("type") == "user":
            emit({"type": "system", "subtype": "init", "session_id": session})
            start(event["message"]["content"])
        elif event.get("id") == permission and "result" in event:
            permission_allowed = event["result"]["outcome"].get("optionId") == "yes"
            permission_event.set()
