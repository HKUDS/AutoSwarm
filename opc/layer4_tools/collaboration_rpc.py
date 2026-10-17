"""Local RPC transport for ``opc-collab`` calls.

The external agent still invokes the normal ``opc-collab`` CLI. When AutoSwarm
spawns that agent, the broker exposes a short-lived local endpoint and injects
its address/token into the environment. The CLI then sends the collaboration
tool call to the already-running broker, so database writes stay in the host
runtime instead of inside the agent sandbox.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import select
import secrets
import shutil
import socket
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


OPC_COLLAB_RPC_PATH = "OPC_COLLAB_RPC_PATH"
OPC_COLLAB_RPC_TOKEN = "OPC_COLLAB_RPC_TOKEN"
OPC_COLLAB_RPC_TRANSPORT = "OPC_COLLAB_RPC_TRANSPORT"
OPC_COLLAB_RPC_HOST = "OPC_COLLAB_RPC_HOST"
OPC_COLLAB_RPC_PORT = "OPC_COLLAB_RPC_PORT"
_RPC_TIMEOUT_SECONDS = 30.0
_RPC_MAX_BYTES = 16 * 1024 * 1024

DispatchCallable = Callable[[str, dict[str, Any]], Awaitable[tuple[dict[str, Any], bool]]]
RpcTransport = Literal["auto", "fifo", "tcp"]


def _infrastructure_error(message: str, *, tool_name: str = "") -> dict[str, Any]:
    payload = {
        "error": str(message or "collaboration broker RPC failed"),
        "error_type": "infrastructure",
        "retryable": True,
    }
    if tool_name:
        payload["tool_name"] = tool_name
    return payload


def fifo_rpc_supported() -> bool:
    """Return whether this runtime can create POSIX FIFOs."""
    return os.name != "nt" and callable(getattr(os, "mkfifo", None))


def default_collaboration_rpc_transport() -> Literal["fifo", "tcp"]:
    return "fifo" if fifo_rpc_supported() else "tcp"


def resolve_collaboration_rpc_transport(
    transport: str | None = "auto",
) -> Literal["fifo", "tcp"]:
    normalized = str(transport or "auto").strip().lower()
    if normalized in {"", "auto"}:
        return default_collaboration_rpc_transport()
    if normalized == "fifo":
        if not fifo_rpc_supported():
            raise RuntimeError("FIFO collaboration RPC is unavailable on this platform")
        return "fifo"
    if normalized == "tcp":
        return "tcp"
    raise ValueError(f"Unsupported collaboration RPC transport: {transport}")


def rpc_env_available(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    token = str(source.get(OPC_COLLAB_RPC_TOKEN, "")).strip()
    if not token:
        return False
    transport = str(source.get(OPC_COLLAB_RPC_TRANSPORT, "")).strip().lower()
    if not transport:
        # Legacy FIFO environment from older brokers.
        return bool(str(source.get(OPC_COLLAB_RPC_PATH, "")).strip())
    if transport == "fifo":
        return bool(str(source.get(OPC_COLLAB_RPC_PATH, "")).strip())
    if transport == "tcp":
        host = str(source.get(OPC_COLLAB_RPC_HOST, "")).strip()
        raw_port = str(source.get(OPC_COLLAB_RPC_PORT, "")).strip()
        try:
            port = int(raw_port)
        except ValueError:
            return False
        return bool(host) and 0 < port <= 65535
    return False


def rpc_env_configured(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return any(
        str(source.get(key, "")).strip()
        for key in (
            OPC_COLLAB_RPC_TRANSPORT,
            OPC_COLLAB_RPC_PATH,
            OPC_COLLAB_RPC_HOST,
            OPC_COLLAB_RPC_PORT,
            OPC_COLLAB_RPC_TOKEN,
        )
    )


def _json_line(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8") + b"\n"


def _decode_rpc_response(raw: bytes, *, tool_name: str) -> tuple[dict[str, Any], bool]:
    try:
        response = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return _infrastructure_error(f"collaboration broker RPC returned invalid JSON: {exc}", tool_name=tool_name), True
    if not isinstance(response, dict):
        return _infrastructure_error("collaboration broker RPC returned a non-object response", tool_name=tool_name), True

    result = response.get("result")
    normalized = result if isinstance(result, dict) else {"result": result}
    is_error = bool(response.get("is_error")) or "error" in normalized
    return normalized, is_error


def _request_payload(
    tool_name: str,
    args: dict[str, Any],
    *,
    token: str,
    response_path: str = "",
) -> dict[str, Any]:
    request = {
        "token": token,
        "tool_name": str(tool_name or "").strip(),
        "args": dict(args or {}),
    }
    if response_path:
        request["response_path"] = response_path
    return request


def _write_fifo_nonblocking(path: Path, payload: dict[str, Any]) -> None:
    data = _json_line(payload)
    if len(data) > _RPC_MAX_BYTES:
        raise ValueError("collaboration RPC request exceeds max payload size")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ENXIO:
            raise RuntimeError("collaboration broker RPC is not accepting requests") from exc
        raise
    try:
        view = memoryview(data)
        while view:
            try:
                written = os.write(fd, view)
                view = view[written:]
            except BlockingIOError:
                _readable, writable, _errors = select.select([], [fd], [], _RPC_TIMEOUT_SECONDS)
                if not writable:
                    raise TimeoutError("collaboration broker RPC write timed out")
    finally:
        os.close(fd)


async def _read_fifo_response(fd: int, *, timeout_seconds: float) -> bytes:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    chunks = bytearray()
    while loop.time() < deadline:
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            await asyncio.sleep(0.02)
            continue
        if chunk:
            chunks.extend(chunk)
            if b"\n" in chunk:
                line, _sep, _rest = bytes(chunks).partition(b"\n")
                return line + b"\n"
            if len(chunks) > _RPC_MAX_BYTES:
                raise RuntimeError("collaboration broker RPC response exceeds max payload size")
        else:
            await asyncio.sleep(0.02)
    raise TimeoutError("collaboration broker RPC response timed out")


async def call_collaboration_rpc(
    tool_name: str,
    args: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Call the broker-owned collaboration RPC endpoint from ``opc-collab``."""
    source = env if env is not None else os.environ
    transport = str(source.get(OPC_COLLAB_RPC_TRANSPORT, "")).strip().lower() or "fifo"
    if transport == "fifo":
        return await _call_fifo_collaboration_rpc(tool_name, args, env=source)
    if transport == "tcp":
        return await _call_tcp_collaboration_rpc(tool_name, args, env=source)
    return _infrastructure_error(
        f"collaboration broker RPC transport is unsupported: {transport}",
        tool_name=tool_name,
    ), True


async def _call_fifo_collaboration_rpc(
    tool_name: str,
    args: dict[str, Any],
    *,
    env: Mapping[str, str],
) -> tuple[dict[str, Any], bool]:
    token = str(env.get(OPC_COLLAB_RPC_TOKEN, "")).strip()
    raw_request_path = str(env.get(OPC_COLLAB_RPC_PATH, "")).strip()
    if not raw_request_path or not token:
        return _infrastructure_error("collaboration broker RPC is not configured", tool_name=tool_name), True
    if not fifo_rpc_supported():
        return _infrastructure_error(
            "FIFO collaboration broker RPC is unavailable on this platform",
            tool_name=tool_name,
        ), True
    request_path = Path(raw_request_path)

    response_dir = request_path.parent / "responses"
    response_path = response_dir / f"{uuid.uuid4().hex}.fifo"
    response_fd: int | None = None
    try:
        response_dir.mkdir(parents=True, exist_ok=True)
        os.mkfifo(response_path, 0o600)
        response_fd = os.open(response_path, os.O_RDONLY | os.O_NONBLOCK)
        request = _request_payload(
            tool_name,
            args,
            token=token,
            response_path=str(response_path),
        )
        _write_fifo_nonblocking(request_path, request)
        raw = await _read_fifo_response(response_fd, timeout_seconds=_RPC_TIMEOUT_SECONDS)
    except Exception as exc:
        return _infrastructure_error(f"collaboration broker RPC failed: {exc}", tool_name=tool_name), True
    finally:
        if response_fd is not None:
            with contextlib.suppress(OSError):
                os.close(response_fd)
        with contextlib.suppress(FileNotFoundError):
            response_path.unlink()

    return _decode_rpc_response(raw, tool_name=tool_name)


async def _call_tcp_collaboration_rpc(
    tool_name: str,
    args: dict[str, Any],
    *,
    env: Mapping[str, str],
) -> tuple[dict[str, Any], bool]:
    host = str(env.get(OPC_COLLAB_RPC_HOST, "")).strip()
    raw_port = str(env.get(OPC_COLLAB_RPC_PORT, "")).strip()
    token = str(env.get(OPC_COLLAB_RPC_TOKEN, "")).strip()
    if not host or not raw_port or not token:
        return _infrastructure_error("collaboration broker RPC is not configured", tool_name=tool_name), True
    try:
        port = int(raw_port)
    except ValueError:
        return _infrastructure_error(
            f"collaboration broker RPC port is invalid: {raw_port}",
            tool_name=tool_name,
        ), True
    if not 0 < port <= 65535:
        return _infrastructure_error(
            f"collaboration broker RPC port is out of range: {port}",
            tool_name=tool_name,
        ), True

    request = _request_payload(tool_name, args, token=token)
    data = _json_line(request)
    if len(data) > _RPC_MAX_BYTES:
        return _infrastructure_error(
            "collaboration RPC request exceeds max payload size",
            tool_name=tool_name,
        ), True

    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host=host, port=port, limit=_RPC_MAX_BYTES + 1024),
            timeout=_RPC_TIMEOUT_SECONDS,
        )
        writer.write(data)
        await asyncio.wait_for(writer.drain(), timeout=_RPC_TIMEOUT_SECONDS)
        raw = await _read_stream_line(reader, timeout_seconds=_RPC_TIMEOUT_SECONDS)
    except Exception as exc:
        return _infrastructure_error(f"collaboration broker RPC failed: {exc}", tool_name=tool_name), True
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    return _decode_rpc_response(raw, tool_name=tool_name)


@dataclass
class CollaborationRpcServer:
    transport: Literal["fifo", "tcp"]
    token: str
    request_path: Path | None = None
    rpc_dir: Path | None = None
    request_fd: int | None = None
    task: asyncio.Task[None] | None = None
    tcp_server: asyncio.AbstractServer | None = None
    host: str = ""
    port: int = 0

    @property
    def client_env(self) -> dict[str, str]:
        if self.transport == "tcp":
            return {
                OPC_COLLAB_RPC_TRANSPORT: "tcp",
                OPC_COLLAB_RPC_HOST: self.host,
                OPC_COLLAB_RPC_PORT: str(self.port),
                OPC_COLLAB_RPC_TOKEN: self.token,
            }
        return {
            OPC_COLLAB_RPC_PATH: str(self.request_path or ""),
            OPC_COLLAB_RPC_TOKEN: self.token,
            OPC_COLLAB_RPC_TRANSPORT: "fifo",
        }

    async def close(self) -> None:
        if self.tcp_server is not None:
            self.tcp_server.close()
            with contextlib.suppress(Exception):
                await self.tcp_server.wait_closed()
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.request_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.request_fd)
        if self.rpc_dir is not None:
            shutil.rmtree(self.rpc_dir, ignore_errors=True)


async def start_collaboration_rpc_server(
    dispatch: DispatchCallable,
    *,
    transport_parent: str | os.PathLike[str] | None = None,
    transport: RpcTransport = "auto",
) -> CollaborationRpcServer | None:
    """Start a broker-local collaboration RPC server."""
    resolved_transport = resolve_collaboration_rpc_transport(transport)
    if resolved_transport == "tcp":
        return await _start_tcp_collaboration_rpc_server(dispatch)
    return await _start_fifo_collaboration_rpc_server(dispatch, transport_parent=transport_parent)


async def _start_fifo_collaboration_rpc_server(
    dispatch: DispatchCallable,
    *,
    transport_parent: str | os.PathLike[str] | None = None,
) -> CollaborationRpcServer:
    parent = Path(transport_parent) if transport_parent else Path(tempfile.gettempdir())
    rpc_dir = Path(tempfile.mkdtemp(prefix="openopc-collab-rpc-", dir=str(parent)))
    request_path = rpc_dir / "requests.fifo"
    token = secrets.token_urlsafe(32)
    os.mkfifo(request_path, 0o600)
    request_fd = os.open(request_path, os.O_RDONLY | os.O_NONBLOCK)

    async def _serve() -> None:
        buffer = bytearray()
        while True:
            try:
                chunk = os.read(request_fd, 65536)
            except BlockingIOError:
                await asyncio.sleep(0.02)
                continue
            if not chunk:
                await asyncio.sleep(0.02)
                continue
            buffer.extend(chunk)
            if len(buffer) > _RPC_MAX_BYTES:
                buffer.clear()
                continue
            while True:
                newline_index = buffer.find(b"\n")
                if newline_index < 0:
                    break
                raw = bytes(buffer[:newline_index])
                del buffer[: newline_index + 1]
                await _handle_request_line(raw)

    async def _handle_request_line(raw: bytes) -> None:
        if not raw:
            return
        try:
            request = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(request, dict):
            return
        response = await _handle_rpc_request(request, dispatch=dispatch, token=token)
        await _respond(request, response)

    task = asyncio.create_task(_serve())
    return CollaborationRpcServer(
        transport="fifo",
        request_path=request_path,
        rpc_dir=rpc_dir,
        token=token,
        request_fd=request_fd,
        task=task,
    )


async def _start_tcp_collaboration_rpc_server(dispatch: DispatchCallable) -> CollaborationRpcServer:
    token = secrets.token_urlsafe(32)

    async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await _read_stream_line(reader, timeout_seconds=_RPC_TIMEOUT_SECONDS)
            try:
                request = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                response = {
                    "result": _infrastructure_error(
                        f"collaboration broker RPC received invalid JSON: {exc}",
                    ),
                    "is_error": True,
                }
            else:
                if isinstance(request, dict):
                    response = await _handle_rpc_request(request, dispatch=dispatch, token=token)
                else:
                    response = {
                        "result": _infrastructure_error("collaboration broker RPC received a non-object request"),
                        "is_error": True,
                    }
            data = _json_line(response)
            if len(data) > _RPC_MAX_BYTES:
                data = _json_line(
                    {
                        "result": _infrastructure_error("collaboration broker RPC response exceeds max payload size"),
                        "is_error": True,
                    }
                )
            writer.write(data)
            await writer.drain()
        except Exception:
            with contextlib.suppress(Exception):
                writer.write(
                    _json_line(
                        {
                            "result": _infrastructure_error("collaboration broker RPC connection failed"),
                            "is_error": True,
                        }
                    )
                )
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(
        _handle_client,
        host="127.0.0.1",
        port=0,
        family=socket.AF_INET,
        limit=_RPC_MAX_BYTES + 1024,
    )
    sockets = server.sockets or []
    if not sockets:
        server.close()
        await server.wait_closed()
        raise RuntimeError("collaboration RPC TCP server did not expose a listening socket")
    host, port = sockets[0].getsockname()[:2]
    return CollaborationRpcServer(
        transport="tcp",
        token=token,
        tcp_server=server,
        host=str(host),
        port=int(port),
    )


async def _read_stream_line(
    reader: asyncio.StreamReader,
    *,
    timeout_seconds: float,
) -> bytes:
    buffer = bytearray()
    while True:
        chunk = await asyncio.wait_for(reader.read(65536), timeout=timeout_seconds)
        if not chunk:
            if buffer:
                return bytes(buffer)
            raise RuntimeError("collaboration broker RPC connection closed before a response")
        buffer.extend(chunk)
        if len(buffer) > _RPC_MAX_BYTES:
            raise RuntimeError("collaboration broker RPC payload exceeds max size")
        newline_index = buffer.find(b"\n")
        if newline_index >= 0:
            return bytes(buffer[: newline_index + 1])


async def _handle_rpc_request(
    request: dict[str, Any],
    *,
    dispatch: DispatchCallable,
    token: str,
) -> dict[str, Any]:
    tool_name = str(request.get("tool_name", "") or "").strip()
    if str(request.get("token", "")) != token:
        return {
            "result": _infrastructure_error("collaboration RPC token rejected", tool_name=tool_name),
            "is_error": True,
        }
    raw_args = request.get("args")
    tool_args = raw_args if isinstance(raw_args, dict) else {}
    try:
        result, is_error = await dispatch(tool_name, tool_args)
        return {"result": result, "is_error": bool(is_error)}
    except Exception as exc:
        return {
            "result": _infrastructure_error(
                f"collaboration broker RPC failed: {exc}",
                tool_name=tool_name,
            ),
            "is_error": True,
        }


async def _respond(request: dict[str, Any], response: dict[str, Any]) -> None:
    response_path = Path(str(request.get("response_path", "") or "").strip())
    if not response_path:
        return
    with contextlib.suppress(Exception):
        _write_fifo_nonblocking(response_path, response)
