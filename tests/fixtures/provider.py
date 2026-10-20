"""Controllable peers for the native bidirectional protocols (no model calls)."""
import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

provider, *args = sys.argv[1:]
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
if "--version" in args:
    print("fixture 2.0")
    raise SystemExit()
session = "fixture-session"


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
    text = prompt + "|session=" + session
    if provider == "codex":
        emit({"method": "item/completed", "params": {"threadId": session, "item": {"type": "agentMessage", "text": text}}})
        emit({"method": "turn/completed", "params": {"threadId": session, "turn": {"id": "native-turn", "status": "failed" if prompt == "fail" else "completed"}}})
    else:
        emit({"type": "result", "session_id": session, "result": text, "is_error": prompt == "fail"})


if provider == "opencode":
    events = []
    decisions = {}
    permissions = {}
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
            path = self.path.split("?")[0]
            if path == "/event":
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
                self.send({"info": {"id": "native-assistant", "sessionID": session, "role": "assistant", "finish": "tool-calls" if prompt == "incomplete" else "stop", **({"error": {"message": "fixture failure"}} if prompt == "fail" else {})}, "parts": [{"type": "text", "text": prompt + "|session=" + session}]})
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), HTTP)
    server.daemon_threads = True
    print(f"opencode server listening on http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
else:
    pending_prompt = None
    marker = None
    for raw in sys.stdin:
        value = json.loads(raw)
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
                else:
                    finished(prompt)
            elif value.get("type") == "control_response":
                allowed = value["response"]["response"]["behavior"] == "allow"
                if allowed:
                    Path(marker).write_text("executed", encoding="utf-8")
                finished("approved" if allowed else "denied")
