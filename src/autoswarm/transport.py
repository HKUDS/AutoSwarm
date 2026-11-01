"""Remote peer transport for AutoSwarm messages.

Design notes
------------
This is an *optional* transport plugin. The local filesystem remains the source
of truth for the agent's own records; the remote transport only forwards
messages and results between peers on different machines.

The protocol is intentionally tiny:

- POST /autoswarm/mail         -> receive a message from a remote peer
- POST /autoswarm/result       -> receive a final result from a remote peer
- POST /autoswarm/approval     -> receive an approval request from a remote peer
- POST /autoswarm/decision     -> receive an approval decision from a remote peer
- GET  /autoswarm/health       -> presence check

Peers are configured manually for now (``--peer-host`` / config) so we can
validate the idea before adding discovery/authentication.

Security
--------
This module does NOT implement encryption or authentication. For a LAN POC we
rely on the operator binding to a specific IP and, optionally, a shared
``AUTOSWARM_SHARED_SECRET`` that signs payloads with HMAC-SHA256. Real
deployments should add TLS and proper peer auth.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .records import Records, SwarmError, read_json, write_json


TRANSPORT_VERSION = "0.1.0"
DEFAULT_PORT = 8787


def _secret() -> bytes:
    value = os.environ.get("AUTOSWARM_SHARED_SECRET", "")
    return value.encode("utf-8") if value else b""


def _sign_payload(payload: bytes, secret: bytes) -> str:
    if not secret:
        return ""
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _verify_signature(payload: bytes, signature: str, secret: bytes) -> bool:
    if not secret:
        return signature == ""
    if not signature:
        return False
    return hmac.compare_digest(_sign_payload(payload, secret), signature)


class _RemoteRequestHandler(BaseHTTPRequestHandler):
    server: "_RemotePeerServer"

    def log_message(self, format, *args):
        # Quiet by default; callers can inspect the transport log if needed.
        pass

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        signature = self.headers.get("X-AutoSwarm-Signature", "")
        if not _verify_signature(body, signature, _secret()):
            self.send_response(401)
            self.end_headers()
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return {}

    def _respond_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        secret = _secret()
        if secret:
            self.send_header("X-AutoSwarm-Signature", _sign_payload(body, secret))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/autoswarm/health":
            self._respond_json(200, {"ok": True, "version": TRANSPORT_VERSION, "home": str(self.server.records.home)})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        data = self._read_json()
        if not data:
            return
        handler = getattr(self.server, f"_handle_{parsed.path.lstrip('/').replace('/', '_')}", None)
        if handler is None:
            self.send_response(404)
            self.end_headers()
            return
        try:
            result = handler(data)
            self._respond_json(200, {"ok": True, "data": result})
        except SwarmError as error:
            self._respond_json(400, {"ok": False, "error": {"code": error.code, "message": str(error)}})
        except Exception as error:
            self._respond_json(500, {"ok": False, "error": {"code": "transport_failed", "message": str(error)}})


class _RemotePeerServer(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, records: Records, host: str, port: int, handler_factory: Callable | None = None):
        self.records = records
        self._handler_factory = handler_factory or _RemoteRequestHandler
        super().__init__((host, port), self._handler_factory)
        self._handlers: dict[str, Callable[[dict], dict]] = {
            "autoswarm_mail": self._handle_mail,
            "autoswarm_result": self._handle_result,
            "autoswarm_approval": self._handle_approval,
            "autoswarm_decision": self._handle_decision,
        }

    def __getattr__(self, name: str):
        if name.startswith("_handle_"):
            key = name[len("_handle_"):]
            if key in self._handlers:
                return self._handlers[key]
        raise AttributeError(name)

    def _handle_mail(self, data: dict) -> dict:
        # A remote peer sent a message addressed to one of our agents.
        # We replay it into the local mailbox so the native agent can receive it.
        from . import messages
        target = data.get("to")
        if not target:
            raise SwarmError("invalid_input", "Remote mail missing 'to'")
        # Verify the target agent exists locally.
        messages.agent(self.records, target)
        message_id = data.get("message_id") or ("msg_" + uuid.uuid4().hex)
        path = messages.agent_path(self.records, target) / "messages" / message_id
        message = {
            "kind": data.get("kind", "agent_message"),
            "message_id": message_id,
            "from": data["from"],
            "to": target,
            "content": data["content"],
            "interrupt": bool(data.get("interrupt")),
            "reply_to": data.get("reply_to"),
            "created_at": data.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "remote_origin": data.get("remote_origin", True),
        }
        write_json(path / "message.json", message)
        # Leave delivery in queued state so pending() will return it for receive().
        write_json(path / "delivery.json", {"state": "queued", "mechanism": "remote_peer", "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        messages.wake(self.records, target)
        return {"message_id": message_id, "state": "submitted"}

    def _handle_result(self, data: dict) -> dict:
        from . import engine
        call_id = data.get("call_id")
        if not call_id:
            raise SwarmError("invalid_input", "Remote result missing call_id")
        directory, task = self.records.require(call_id)
        # Only accept a remote result if this call was delegated to a remote peer.
        if task.get("remote_peer") != data.get("from_peer"):
            raise SwarmError("peer_mismatch", "This call is not delegated to the sending peer")
        write_json(directory / "result.json", data["result"])
        write_json(directory / "delivery.json", {"state": "delivered", "mechanism": "remote_peer", "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return {"state": "delivered"}

    def _handle_approval(self, data: dict) -> dict:
        from . import approvals
        call_id = data.get("call_id")
        request_id = data.get("request_id")
        if not call_id or not request_id:
            raise SwarmError("invalid_input", "Remote approval missing call_id or request_id")
        approvals.location(self.records, call_id, request_id).mkdir(parents=True, exist_ok=True)
        write_json(approvals.location(self.records, call_id, request_id) / "request.json", data["request"])
        return {"state": "submitted"}

    def _handle_decision(self, data: dict) -> dict:
        from . import approvals
        call_id = data.get("call_id")
        request_id = data.get("request_id")
        if not call_id or not request_id:
            raise SwarmError("invalid_input", "Remote decision missing call_id or request_id")
        write_json(approvals.location(self.records, call_id, request_id) / "decision.json", data["decision"])
        return {"state": "delivered"}


class RemoteTransport:
    """Run a local HTTP endpoint and forward outbound traffic to known peers."""

    def __init__(self, records: Records, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
        self.records = records
        self.host = host
        self.port = port
        self._server: _RemotePeerServer | None = None
        self._thread: threading.Thread | None = None
        self._peers: dict[str, str] = {}

    def add_peer(self, name: str, url: str):
        self._peers[name] = url.rstrip("/")

    def start(self):
        # macOS can keep a recently-closed port in TIME_WAIT even with SO_REUSEADDR,
        # so we let the OS pick a free port if the requested one is unavailable.
        try:
            self._server = _RemotePeerServer(self.records, self.host, self.port)
        except OSError as error:
            if error.errno != 48:
                raise
            self._server = _RemotePeerServer(self.records, self.host, 0)
            self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def send_to_peer(self, peer_name: str, endpoint: str, payload: dict) -> dict:
        import urllib.request
        url = self._peers.get(peer_name)
        if not url:
            raise SwarmError("peer_not_found", f"No remote peer configured for {peer_name}")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        secret = _secret()
        if secret:
            headers["X-AutoSwarm-Signature"] = _sign_payload(body, secret)
        req = urllib.request.Request(f"{url}/autoswarm/{endpoint}", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as error:
            raise SwarmError("remote_peer_failed", f"Could not reach {peer_name}: {error}")

    def health(self, peer_name: str) -> dict:
        import urllib.request
        url = self._peers.get(peer_name)
        if not url:
            raise SwarmError("peer_not_found", f"No remote peer configured for {peer_name}")
        req = urllib.request.Request(f"{url}/autoswarm/health", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
