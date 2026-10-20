"""Small stdlib JSON-RPC/WebSocket client for an existing Codex host."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
from urllib.parse import urlsplit

from .records import SwarmError


class RPCError(SwarmError):
    def __init__(self, error):
        super().__init__("host_rpc_error", str(error.get("message", error)))
        self.details = error


class RPC:
    def __init__(self, endpoint: str, token: str | None = None):
        url = urlsplit(endpoint)
        self.sock = None
        try:
            if url.scheme == "unix":
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(url.path)
                host, path = "localhost", "/"
            elif url.scheme in {"ws", "wss"}:
                self.sock = socket.create_connection((url.hostname, url.port or (443 if url.scheme == "wss" else 80)))
                if url.scheme == "wss":
                    self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=url.hostname)
                host, path = url.netloc, url.path or "/"
                if url.query:
                    path += "?" + url.query
            else:
                raise SwarmError("invalid_endpoint", "Codex endpoint must use unix://, ws:// or wss://")
            key = base64.b64encode(os.urandom(16)).decode()
            headers = [f"GET {path} HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
            if token:
                headers.append("Authorization: Bearer " + token)
            self.sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
            response = b""
            while not response.endswith(b"\r\n\r\n"):
                response += self._read(1)
            fields = response.decode().split("\r\n")
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            values = {k.lower(): v.strip() for line in fields[1:] if ":" in line for k, v in [line.split(":", 1)]}
            if " 101 " not in fields[0] or values.get("sec-websocket-accept") != expected:
                raise SwarmError("host_connection_failed", "Host rejected WebSocket handshake: " + fields[0])
            self.sequence = 0
            self.request("initialize", {"clientInfo": {"name": "autoswarm", "version": "2.0.0.dev0"}})
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def _read(self, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            part = self.sock.recv(size - len(output))
            if not part:
                raise SwarmError("host_disconnected", "Parent host disconnected")
            output.extend(part)
        return bytes(output)

    def frame(self, payload: bytes, opcode: int = 1):
        length = len(payload)
        head = bytes([0x80 | opcode])
        if length < 126:
            head += bytes([0x80 | length])
        elif length < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))

    def send(self, value):
        self.frame(json.dumps(value, ensure_ascii=False).encode())

    def receive(self):
        message = bytearray()
        while True:
            first, second = self._read(2)
            opcode, length = first & 15, second & 127
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if second & 128 else None
            payload = self._read(length)
            if mask:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 8:
                raise SwarmError("host_disconnected", "Parent host closed the connection")
            if opcode == 9:
                self.frame(payload, 10)
                continue
            if opcode == 10:
                continue
            message.extend(payload)
            if first & 128:
                return json.loads(message)

    def request(self, method: str, params: dict):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RPCError(message["error"])
                return message.get("result", {})
            if "method" in message and "id" in message:
                # Never answer another client's approval request affirmatively.
                self.send({"id": message["id"], "error": {"code": -32601, "message": "AutoSwarm does not provide interactive approvals"}})

    def close(self):
        if self.sock:
            self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
