"""Return results to the original host, never to a guessed or new session."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .records import Records, SwarmError, now, read_json, write_json
from .wire import RPC, RPCError
from .platform import session_lease


def detect_parent() -> dict | None:
    if os.environ.get("AUTOSWARM_PARENT"):
        return json.loads(os.environ["AUTOSWARM_PARENT"])
    if os.environ.get("CODEX_THREAD_ID"):
        home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        endpoint = os.environ.get("AUTOSWARM_CODEX_ENDPOINT")
        candidate = home / "app-server-control" / "app-server-control.sock"
        try:
            if not endpoint and candidate.exists():
                endpoint = "unix://" + str(candidate)
        except OSError:
            # Some host sandboxes cannot even stat the control directory.
            # Keep the exact thread binding; explicit wait still works.
            pass
        return {"host": "codex", "session_id": os.environ["CODEX_THREAD_ID"], "endpoint": endpoint}
    return None


def result_message(result: dict, directory: Path) -> str:
    if result.get("kind") == "approval_required":
        from .approvals import message
        return message(result)
    return (
        f"AutoSwarm child result [{result['call_id']}]. This is a child agent's output. "
        "Continue the user's existing task using this result.\n"
        f"Provider: {result['provider']}\nOutcome: {result['outcome']}\n"
        f"Result file: {directory / 'result.json'}\n\n"
        + result.get("content", "")
        + ("\n\nExecution error: " + result["error"] if result.get("error") else "")
    )


def http_headers(parent: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    password = os.environ.get(parent.get("password_env", "OPENCODE_SERVER_PASSWORD"))
    if password:
        username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
        headers["Authorization"] = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    return headers


def _http(parent: dict, method: str, path: str, body=None):
    endpoint = parent.get("endpoint", "")
    if not endpoint.startswith(("http://", "https://")):
        raise SwarmError("invalid_endpoint", "OpenCode host requires its existing HTTP server endpoint")
    if urllib.parse.urlsplit(endpoint).username:
        raise SwarmError("invalid_endpoint", "Use credential environment variables, not credentials in endpoint URLs")
    headers = http_headers(parent)
    query = "?directory=" + urllib.parse.quote(parent["workspace"], safe="") if parent.get("workspace") else ""
    request = urllib.request.Request(endpoint.rstrip("/") + path + query, data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        value = response.read()
        return json.loads(value) if value else None


def deliver_codex(parent: dict, result: dict, directory: Path) -> dict:
    endpoint = parent.get("endpoint")
    if not endpoint:
        raise SwarmError("parent_endpoint_required", "No connection to the original Codex host; use wait/receive or bind its app-server endpoint")
    token = os.environ.get(parent.get("token_env", "AUTOSWARM_CODEX_TOKEN"))
    thread_id = parent["session_id"]
    message_id = str(uuid.uuid5(uuid.NAMESPACE_URL, result["call_id"] + "/" + result["request_id"])) if result.get("request_id") else str(uuid.UUID(result["call_id"][5:]))
    text = result_message(result, directory)
    with RPC(endpoint, token) as rpc:
        rpc.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
        while True:
            thread = rpc.request("thread/read", {"threadId": thread_id})["thread"]
            status = thread.get("status", {}).get("type")
            data = {"threadId": thread_id, "clientUserMessageId": message_id, "input": [{"type": "text", "text": text}]}
            try:
                if status == "active":
                    turns = rpc.request("thread/turns/list", {"threadId": thread_id, "sortDirection": "desc"}).get("data", [])
                    active = next((turn for turn in turns if turn.get("status") == "inProgress"), None)
                    if active is None:
                        raise SwarmError("parent_turn_unknown", "Parent is active but did not expose an active turn")
                    data["expectedTurnId"] = active["id"]
                    response = rpc.request("turn/steer", data)
                    return {"state": "delivered", "mechanism": "codex_turn_steer", "turn_id": response.get("turnId"), "continued": True}
                if status not in {"idle", "notLoaded"}:
                    raise SwarmError("parent_unavailable", f"Parent thread status: {status}")
                response = rpc.request("turn/start", data)
                return {"state": "delivered", "mechanism": "codex_turn_start", "turn_id": response.get("turn", {}).get("id"), "continued": True}
            except RPCError as error:
                # Native turn preconditions arbitrate a busy/idle race.
                if any(word in str(error).lower() for word in ("no active turn", "expectedturnid", "expected turn", "already has an active", "turn is not active")):
                    continue
                raise


def deliver_opencode(parent: dict, result: dict, directory: Path) -> dict:
    session = urllib.parse.quote(parent["session_id"], safe="")
    # A synchronous native prompt observes the returned assistant message, rather
    # than mistaking HTTP 204 queue acceptance for a completed continuation.
    response = _http(parent, "POST", f"/session/{session}/message", {"parts": [{"type": "text", "text": result_message(result, directory)}]})
    if not isinstance(response, dict) or not response.get("info") or response["info"].get("role") != "assistant":
        raise SwarmError("parent_receipt_missing", "OpenCode returned no assistant message receipt")
    if response["info"].get("error"):
        raise SwarmError("parent_continuation_failed", json.dumps(response["info"]["error"], ensure_ascii=False))
    return {"state": "delivered", "mechanism": "opencode_session_message", "message_id": response["info"].get("id"), "continued": True}


def deliver(records: Records, call_id: str, *, explicit: bool = False, request_id: str | None = None) -> dict:
    directory, _ = records.require(call_id)
    if request_id:
        from .approvals import location
        directory = location(records, call_id, request_id)
    try:
        lease = session_lease(directory / "delivery.lock")
    except SwarmError:
        return read_json(directory / "delivery.json", {"state": "pending", "continued": False, "error_code": "delivery_in_progress"})
    try:
        return _deliver(records, call_id, explicit=explicit, request_id=request_id)
    finally:
        lease.close()


def _deliver(records: Records, call_id: str, *, explicit: bool = False, request_id: str | None = None) -> dict:
    directory, task = records.require(call_id)
    result_file = directory / "result.json"
    if request_id:
        from .approvals import location, delegated
        directory = location(records, call_id, request_id)
        result_file = directory / "request.json"
    result = read_json(result_file)
    if result is None:
        raise SwarmError("result_not_ready", "The requested child event is not available")
    if request_id:
        if (directory / "resolution.json").exists() or (directory / "decision.json").exists():
            return {"state": "resolved", "continued": False}
        result = {**result, "delegate_to_parent": delegated(records, task)}
    parent = task.get("parent")
    prior = read_json(directory / "delivery.json", {})
    if prior.get("state") in {"delivered", "submitted"}:
        return prior
    if not parent or (not explicit and (task.get("return_mode") == "wait" or (directory / "received.json").exists())):
        receipt = {"state": "pull", "continued": False, "message": "Result is available through wait/receive"}
    elif parent.get("mode") == "hook":
        receipt = {"state": "pending", "mechanism": "host_hook", "continued": False}
    else:
        try:
            if parent.get("host") == "codex":
                receipt = deliver_codex(parent, result, directory)
            elif parent.get("host") == "opencode":
                receipt = deliver_opencode(parent, result, directory)
            elif parent.get("host") == "command":
                argv = parent.get("command")
                if not isinstance(argv, list) or not argv:
                    raise SwarmError("invalid_parent", "Command bridge requires an argv array")
                process = subprocess.run(argv, input=json.dumps({"parent": parent, "result": result, "result_file": str(result_file)}), text=True, capture_output=True, encoding="utf-8")
                if process.returncode:
                    raise SwarmError("parent_delivery_failed", process.stderr or "Host bridge failed")
                receipt = json.loads(process.stdout)
                if receipt.get("state") != "delivered" or not isinstance(receipt.get("continued"), bool):
                    raise SwarmError("parent_receipt_missing", "Bridge must return state=delivered and an explicit continued boolean")
            else:
                raise SwarmError("unsupported_parent", "Use Claude hooks, a native Codex/OpenCode endpoint, or a command bridge")
        except (SwarmError, OSError, ValueError, urllib.error.URLError) as error:
            transient = isinstance(error, OSError) and not isinstance(error, SwarmError)
            if isinstance(error, urllib.error.HTTPError):
                transient = error.code >= 500 or error.code in {409, 429}
            if isinstance(error, SwarmError):
                transient = error.code == "host_disconnected"
            receipt = {"state": "pending", "continued": False, "error": str(error), "error_code": getattr(error, "code", "parent_delivery_failed"), "retryable": transient}
    receipt.update({"call_id": call_id, "at": now()})
    if request_id:
        receipt["request_id"] = request_id
    write_json(directory / "delivery.json", receipt)
    return receipt
