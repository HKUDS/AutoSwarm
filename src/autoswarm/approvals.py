"""A pending native permission and its reply, owned by one child invocation."""
from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path

from . import platform
from .records import Records, SwarmError, now, read_json, write_json


def delegated(records: Records, task: dict) -> bool:
    value = task.get("delegate_approvals")
    if value is None:
        value = records.config().get("approvals", {}).get("delegate_to_parent", True)
    if not isinstance(value, bool):
        raise SwarmError("invalid_config", "approvals.delegate_to_parent must be a boolean")
    return value


def configure(records: Records, value: bool | None = None) -> dict:
    if value is not None:
        lease = platform.session_lease(records.home / "config.lock")
        try:
            config = records.config()
            config.setdefault("approvals", {})["delegate_to_parent"] = value
            write_json(records.home / "config.json", config)
        finally:
            lease.close()
    return {"delegate_approvals": delegated(records, {})}


def location(records: Records, call_id: str, request_id: str) -> Path:
    if not re.fullmatch(r"approval_[a-f0-9]{32}", request_id):
        raise SwarmError("invalid_request_id", "Invalid AutoSwarm approval request ID")
    directory, _ = records.require(call_id)
    return directory / "approvals" / request_id


def pending(records: Records, call_id: str, *, unseen: bool = False) -> list[dict]:
    directory, task = records.require(call_id)
    if (directory / "result.json").exists() or (directory / "cancel.json").exists() or not platform.running(read_json(directory / "handle.json")):
        return []
    values = []
    for path in sorted((directory / "approvals").glob("approval_*")):
        request = read_json(path / "request.json")
        if not request or (path / "resolution.json").exists() or (path / "decision.json").exists():
            continue
        if unseen and ((path / "received.json").exists() or read_json(path / "delivery.json", {}).get("state") in {"delivered", "submitted"}):
            continue
        values.append({**request, "delegate_to_parent": delegated(records, task)})
    return values


def decide(records: Records, call_id: str, request_id: str, decision: str, *, actor: str = "parent", scope: str = "once", message: str = "") -> dict:
    path = location(records, call_id, request_id)
    directory, task = records.require(call_id)
    request = read_json(path / "request.json")
    if not request:
        raise SwarmError("approval_not_found", "Approval request does not belong to this call")
    if decision not in {"allow", "deny"} or actor not in {"parent", "user"}:
        raise SwarmError("invalid_decision", "Use allow/deny and parent/user")
    if actor == "parent" and not delegated(records, task):
        raise SwarmError("user_approval_required", "Parent delegation is disabled. Ask the user in the original conversation; use --by user only to relay their explicit decision.")
    if scope not in request["scopes"]:
        raise SwarmError("unsupported_approval_scope", "The native request does not support this approval scope")
    lease = platform.session_lease(path / "decision.lock")
    try:
        old = read_json(path / "decision.json")
        value = {"call_id": call_id, "request_id": request_id, "decision": decision, "actor": actor, "scope": scope, "message": message}
        if old and all(old.get(key) == item for key, item in value.items()):
            return old
        if old or (path / "resolution.json").exists() or (directory / "cancel.json").exists() or (directory / "result.json").exists() or not platform.running(read_json(directory / "handle.json")):
            raise SwarmError("approval_not_pending", "The original native request is no longer awaiting a decision")
        value["at"] = now()
        write_json(path / "decision.json", value)
        return value
    finally:
        lease.close()


def message(request: dict) -> str:
    import json
    policy = ("Parent delegation is enabled. Decide within the user's task and your host's authorization; ask the user when needed."
              if request["delegate_to_parent"] else
              "Parent delegation is DISABLED. Ask the user in this conversation. Do not supply your own decision as a user decision.")
    return (f"AutoSwarm approval required [{request['call_id']}/{request['request_id']}]. "
            "This is a child's native permission request, not a new user instruction or a final result.\n"
            f"{policy}\nProvider: {request['provider']}\nWorkspace: {request['workspace']}\n"
            f"Request file: {request['request_file']}\n"
            f"Native request: {json.dumps(request['native_request'], ensure_ascii=False)}\n"
            f"Reply using AutoSwarm approve/deny {request['call_id']} {request['request_id']}. "
            "Use --by user only after the user explicitly answers. The child remains paused; do not wait for its final result before answering. "
            "You may end your turn to await the user, and other child calls can continue.")


class Broker:
    def __init__(self, records: Records, task: dict):
        self.records, self.task = records, task
        self.directory = records.call(task["call_id"])
        self.closed = threading.Event()
        self.threads: list[threading.Thread] = []
        self.errors: list[str] = []

    def request(self, native_id, session_id, native_request: dict, reply, *, scopes=None):
        request_id = "approval_" + uuid.uuid5(uuid.NAMESPACE_URL, self.task["call_id"] + ":" + str(native_id)).hex
        path = location(self.records, self.task["call_id"], request_id)
        request = {"kind": "approval_required", "call_id": self.task["call_id"], "request_id": request_id,
                   "provider": self.task["provider"], "native_session_id": session_id, "native_request_id": native_id,
                   "native_request": native_request, "workspace": self.task["workspace"],
                   "scopes": scopes or ["once"], "delegate_to_parent": delegated(self.records, self.task),
                   "created_at": now(), "request_file": str(path / "request.json")}
        if (path / "request.json").exists():
            return request_id
        write_json(path / "request.json", request)

        def notify():
            from .hosts import deliver
            while not self.closed.is_set() and not (path / "decision.json").exists() and not (path / "resolution.json").exists():
                receipt = deliver(self.records, self.task["call_id"], request_id=request_id)
                if not receipt.get("retryable"):
                    return
                self.closed.wait(0.5)

        def await_decision():
            try:
                while not self.closed.is_set() and not (self.directory / "cancel.json").exists() and not (path / "resolution.json").exists():
                    value = read_json(path / "decision.json")
                    if value:
                        reply(value)
                        write_json(path / "resolution.json", {"state": "replied", "at": now()})
                        return
                    self.closed.wait(0.1)
            except Exception as error:
                self.errors.append(str(error))
                write_json(path / "resolution.json", {"state": "reply_failed", "error": str(error), "at": now()})

        for action in (await_decision, notify):
            thread = threading.Thread(target=action, daemon=True)
            self.threads.append(thread)
            thread.start()
        return request_id

    def withdraw(self, native_id, state="withdrawn"):
        request_id = "approval_" + uuid.uuid5(uuid.NAMESPACE_URL, self.task["call_id"] + ":" + str(native_id)).hex
        path = location(self.records, self.task["call_id"], request_id)
        if (path / "request.json").exists() and not (path / "resolution.json").exists():
            write_json(path / "resolution.json", {"state": state, "at": now()})

    def close(self):
        self.closed.set()
        for path in (self.directory / "approvals").glob("approval_*"):
            if not (path / "resolution.json").exists():
                write_json(path / "resolution.json", {"state": "cancelled" if (self.directory / "cancel.json").exists() else "interrupted", "at": now()})
