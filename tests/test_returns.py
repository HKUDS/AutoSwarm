from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import socketserver
import struct
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from autoswarm.hosts import deliver, detect_parent
from autoswarm import messages
from autoswarm.records import Records, read_json, write_json


class WebSocketPeer(socketserver.BaseRequestHandler):
    def exact(self, length):
        data = b""
        while len(data) < length:
            part = self.request.recv(length - len(data))
            if not part:
                raise EOFError()
            data += part
        return data

    def handle(self):
        self.request.settimeout(10)  # Bound the test harness only.
        try:
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                header += self.exact(1)
            key = next(line.split(":", 1)[1].strip() for line in header.decode().split("\r\n") if line.lower().startswith("sec-websocket-key:"))
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.request.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n").encode())
            while True:
                first, second = self.exact(2)
                length = second & 127
                if length == 126:
                    length = struct.unpack("!H", self.exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self.exact(8))[0]
                mask = self.exact(4) if second & 128 else None
                body = self.exact(length)
                if mask:
                    body = bytes(value ^ mask[index % 4] for index, value in enumerate(body))
                message = json.loads(body)
                self.server.messages.append(message)
                if "id" not in message:
                    continue
                method, params = message["method"], message.get("params", {})
                error = None
                if method == "thread/read":
                    result = {"thread": {"id": "original", "status": {"type": self.server.status}}}
                elif method == "thread/turns/list":
                    result = {"data": [{"id": "active-turn", "status": "inProgress"}]}
                elif method == "turn/start":
                    result = {"turn": {"id": "continued-turn"}}
                elif method == "turn/steer":
                    if self.server.race:
                        self.server.race = False
                        self.server.status = "idle"
                        error = {"code": -32600, "message": "no active turn"}
                    else:
                        result = {"turnId": params["expectedTurnId"]}
                else:
                    result = {}
                response = {"id": message["id"], **({"error": error} if error else {"result": result})}
                body = json.dumps(response).encode()
                head = b"\x81" + (bytes([len(body)]) if len(body) < 126 else b"\x7e" + struct.pack("!H", len(body)))
                self.request.sendall(head + body)
        except (EOFError, ConnectionError, OSError):
            return


class HTTPPeer(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"info": {"id": "parent-assistant-message", "role": "assistant"}, "parts": [{"type": "text", "text": "continued"}]}).encode())

    def log_message(self, *args):
        pass


class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.records = Records(Path(self.temporary.name))
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.temporary.cleanup()

    def websocket(self, status, race=False):
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), WebSocketPeer)
        server.daemon_threads = True
        server.status, server.race, server.messages = status, race, []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return server, f"ws://127.0.0.1:{server.server_address[1]}"

    def seed(self, parent, content="actual child output"):
        call_id = "call_" + "b" * 32
        directory = self.records.call(call_id)
        write_json(directory / "task.json", {"call_id": call_id, "provider": "codex", "parent": parent})
        write_json(directory / "result.json", {"call_id": call_id, "provider": "codex", "outcome": "completed", "content": content, "error": None})
        return call_id

    def test_idle_codex_starts_original_thread(self):
        server, endpoint = self.websocket("idle")
        call_id = self.seed({"host": "codex", "session_id": "original", "endpoint": endpoint})
        receipt = deliver(self.records, call_id)
        self.assertTrue(receipt["continued"])
        self.assertEqual(receipt["turn_id"], "continued-turn")
        starts = [message for message in server.messages if message["method"] == "turn/start"]
        self.assertEqual(starts[0]["params"]["threadId"], "original")
        self.assertFalse(any(message["method"] == "thread/start" for message in server.messages))

    def send_main_message(self, parent):
        controller = "main_" + "c" * 32
        child = self.seed(None)
        write_json(messages.agent_path(self.records, controller) / "agent.json", {"agent_id": controller, "call_id": None, "owner_id": None, "parent": parent})
        write_json(messages.agent_path(self.records, child) / "agent.json", {"agent_id": child, "call_id": child, "owner_id": controller})
        sent = messages.send(self.records, controller, "question from child 中文", sender=child, interrupt=True)
        end = time.monotonic() + 10  # Test harness only.
        while time.monotonic() < end:
            receipt = messages.inspect(self.records, sent["message_id"])["delivery"]
            if receipt["state"] == "submitted":
                return sent, receipt
            time.sleep(.01)
        self.fail("Host did not accept the message: " + repr(receipt))

    def test_child_message_uses_busy_original_codex_host_through_one_shot_worker(self):
        server, endpoint = self.websocket("active")
        sent, receipt = self.send_main_message({"host": "codex", "session_id": "original", "endpoint": endpoint})
        self.assertEqual(receipt["mechanism"], "codex_turn_steer")
        steer = next(value for value in server.messages if value["method"] == "turn/steer")
        self.assertEqual(steer["params"]["threadId"], "original")
        self.assertIn(sent["message_id"], steer["params"]["input"][0]["text"])
        self.assertIn("question from child 中文", steer["params"]["input"][0]["text"])

    def test_child_message_uses_original_opencode_host_without_blocking_sender(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), HTTPPeer)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        sent, receipt = self.send_main_message({"host": "opencode", "session_id": "original", "endpoint": f"http://127.0.0.1:{server.server_address[1]}"})
        self.assertEqual(receipt["mechanism"], "opencode_session_message")
        self.assertEqual(server.requests[0][0], "/session/original/message")
        self.assertIn(sent["message_id"], server.requests[0][1]["parts"][0]["text"])

    def test_busy_codex_steers_exact_active_turn(self):
        server, endpoint = self.websocket("active")
        call_id = self.seed({"host": "codex", "session_id": "original", "endpoint": endpoint}, "完整结果🐝\n" * 20000)
        receipt = deliver(self.records, call_id)
        self.assertEqual(receipt["mechanism"], "codex_turn_steer")
        steer = next(message for message in server.messages if message["method"] == "turn/steer")
        self.assertEqual(steer["params"]["expectedTurnId"], "active-turn")
        self.assertIn("完整结果🐝", steer["params"]["input"][0]["text"])

    def test_busy_idle_race_is_resolved_by_native_precondition(self):
        server, endpoint = self.websocket("active", race=True)
        call_id = self.seed({"host": "codex", "session_id": "original", "endpoint": endpoint})
        self.assertEqual(deliver(self.records, call_id)["mechanism"], "codex_turn_start")

    def test_saved_delivery_is_not_sent_again(self):
        server, endpoint = self.websocket("idle")
        call_id = self.seed({"host": "codex", "session_id": "original", "endpoint": endpoint})
        first = deliver(self.records, call_id)
        count = len(server.messages)
        self.assertEqual(deliver(self.records, call_id), first)
        self.assertEqual(len(server.messages), count)

    def test_approval_and_final_result_have_distinct_receipts_in_original_codex(self):
        server, endpoint = self.websocket("active")
        call_id = self.seed({"host": "codex", "session_id": "original", "endpoint": endpoint})
        directory = self.records.call(call_id)
        request_id = "approval_" + "a" * 32
        path = directory / "approvals" / request_id
        write_json(path / "request.json", {"kind": "approval_required", "call_id": call_id, "request_id": request_id, "provider": "claude-code", "workspace": "/isolated-project", "request_file": str(path / "request.json"), "native_request": {"tool_name": "Bash", "input": {"command": "test-command"}}})
        approval = deliver(self.records, call_id, request_id=request_id)
        self.assertTrue(approval["continued"])
        self.assertFalse((directory / "delivery.json").exists())
        self.assertEqual(read_json(path / "delivery.json")["request_id"], request_id)
        final = deliver(self.records, call_id)
        self.assertTrue(final["continued"])
        steers = [value["params"] for value in server.messages if value["method"] == "turn/steer"]
        self.assertEqual(len(steers), 2)
        self.assertTrue(all(value["threadId"] == "original" for value in steers))
        self.assertNotEqual(steers[0]["clientUserMessageId"], steers[1]["clientUserMessageId"])
        self.assertIn("approval required", steers[0]["input"][0]["text"])
        self.assertIn("child result", steers[1]["input"][0]["text"])

    def test_disconnection_retains_result_and_pending_receipt(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        call_id = self.seed({"host": "codex", "session_id": "original", "endpoint": f"ws://127.0.0.1:{port}"})
        receipt = deliver(self.records, call_id)
        self.assertEqual(receipt["state"], "pending")
        self.assertTrue(receipt["retryable"])
        self.assertEqual(read_json(self.records.call(call_id) / "result.json")["content"], "actual child output")

    def test_opencode_observes_native_assistant_receipt(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), HTTPPeer)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        call_id = self.seed({"host": "opencode", "session_id": "original", "endpoint": f"http://127.0.0.1:{server.server_address[1]}"})
        receipt = deliver(self.records, call_id)
        self.assertEqual(receipt["message_id"], "parent-assistant-message")
        self.assertTrue(receipt["continued"])
        self.assertEqual(server.requests[0][0], "/session/original/message")

    def test_missing_binding_is_explicit_not_fake_success(self):
        call_id = self.seed({"host": "codex", "session_id": "original"})
        receipt = deliver(self.records, call_id)
        self.assertEqual(receipt["state"], "pending")
        self.assertFalse(receipt["continued"])

    def test_inaccessible_control_socket_preserves_explicit_wait_binding(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "original", "USERPROFILE": str(self.records.home), "HOME": str(self.records.home)}, clear=True), patch("pathlib.Path.exists", side_effect=PermissionError("sandbox")):
            self.assertEqual(detect_parent(), {"host": "codex", "session_id": "original", "endpoint": None})
