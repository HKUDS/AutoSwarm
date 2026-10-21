"""Durable native permissions and interactions, owned by one child invocation."""
from __future__ import annotations

import re
import threading
import uuid
from contextlib import ExitStack
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
        values.append({**request, "delegate_to_parent": delegated(records, task), **({"reply_error": read_json(path / "reply-error.json")} if (path / "reply-error.json").exists() else {})})
    return values


def decide(records: Records, call_id: str, request_id: str, decision: str, *, actor: str = "parent", scope: str = "once", message: str = "") -> dict:
    path = location(records, call_id, request_id)
    directory, task = records.require(call_id)
    request = read_json(path / "request.json")
    if not request:
        raise SwarmError("approval_not_found", "Approval request does not belong to this call")
    if request.get("kind") == "interaction_required":
        raise SwarmError("answer_required", "This is a native question/form, not permission. Use answer with its response_format.")
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


def answer(records: Records, call_id: str, request_id: str, response: dict, *, actor="parent") -> dict:
    from .interactions import validate
    path = location(records, call_id, request_id)
    directory, task = records.require(call_id)
    request = read_json(path / "request.json")
    if not request or request.get("kind") != "interaction_required":
        raise SwarmError("interaction_not_found", "Use approve/deny for a permission; answer requires a native interaction")
    if actor not in {"parent", "user"}:
        raise SwarmError("invalid_actor", "Use parent or user")
    if actor == "parent" and (request.get("user_required") or not delegated(records, task)):
        raise SwarmError("user_answer_required", "Relay an actual user answer with --by user; do not invent consent or user input")
    try:
        validate(request, response)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise SwarmError("invalid_answer", "Response does not match the native request; inspect response_help and the original payload: " + str(error)) from error
    lease = platform.session_lease(path / "decision.lock")
    try:
        old = read_json(path / "decision.json")
        value = {"call_id": call_id, "request_id": request_id, "response": response, "actor": actor}
        if old and all(old.get(k) == v for k, v in value.items()):
            return old
        if old or (path / "resolution.json").exists() or (directory / "cancel.json").exists() or (directory / "result.json").exists() or not platform.running(read_json(directory / "handle.json")):
            raise SwarmError("interaction_not_pending", "The original native interaction is no longer awaiting an answer")
        value["at"] = now()
        write_json(path / "decision.json", value)
        return value
    finally:
        lease.close()


def message(request: dict) -> str:
    import json
    if request.get("kind") == "interaction_required":
        return (f"AutoSwarm interaction required [{request['call_id']}/{request['request_id']}]. "
                "This is a child's native question, confirmation or form; it is not a final result or new authorization.\n"
                f"Provider: {request['provider']}\nRequest file: {request['request_file']}\n"
                f"Native request: {json.dumps(request['native_request'], ensure_ascii=False)}\n"
                f"Response format: {request['response_format']} — {request['response_help']}\n"
                f"User answer required: {request['user_required']}; parent delegation: {request['delegate_to_parent']}.\n"
                f"Reply with answer {request['call_id']} {request['request_id']} --input <JSON-file>. "
                "Answer within existing task authorization; ask the user when required or uncertain. "
                "Use --by user only for their explicit answer. Handle this request before waiting again for the child's final result. "
                "If native delivery fails, inspect reply_error and the original request before correcting the answer or recovering the task.")
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
        self.lock = threading.RLock()
        self.epoch = 0
        self.reply_context = threading.local()
        self.native_requests = {}

    def check_reply(self):
        path = getattr(self.reply_context, "path", None)
        if path and (self.closed.is_set() or (path / "resolution.json").exists() or (self.directory / "cancel.json").exists()):
            raise SwarmError("interaction_not_pending", "The original native request was withdrawn before submission")

    def new_turn(self):
        with self.lock:
            self.epoch += 1

    def key(self, native_id, epoch=None):
        return "approval_" + uuid.uuid5(uuid.NAMESPACE_URL, self.task["call_id"] + ":" + str(native_id) + ":" + str(self.epoch if epoch is None else epoch)).hex

    def request(self, native_id, session_id, native_request: dict, reply, *, scopes=None, interaction=None, user_required=False, on_receive=None):
        request_id = self.key(native_id)
        path = location(self.records, self.task["call_id"], request_id)
        self.native_requests[str(native_id)] = path
        request = {"kind": "approval_required", "call_id": self.task["call_id"], "request_id": request_id,
                   "provider": self.task["provider"], "native_session_id": session_id, "native_request_id": native_id,
                   "native_turn": self.epoch, "native_request": native_request, "workspace": self.task["workspace"],
                   "scopes": scopes or ["once"], "delegate_to_parent": delegated(self.records, self.task),
                   "created_at": now(), "request_file": str(path / "request.json")}
        if interaction:
            from .interactions import contract
            request.update(kind="interaction_required", response_format=interaction,
                           response_help=contract(interaction), user_required=user_required)
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
            engaged = False
            while not self.closed.is_set() and not (self.directory / "cancel.json").exists() and not (path / "resolution.json").exists():
                if on_receive and not engaged and ((path / "decision.json").exists() or (path / "received.json").exists() or read_json(path / "delivery.json", {}).get("state") in {"submitted", "delivered"}):
                    engaged = True
                    try:
                        on_receive()
                    except Exception as error:
                        self.issue("native_engagement_failed", {"request_id": request_id, "error": str(error)})
                value = read_json(path / "decision.json")
                if not value:
                    self.closed.wait(0.1)
                    continue
                attempt = path / "attempts" / (uuid.uuid4().hex + ".json")
                write_json(attempt, {"decision": value, "state": "submitting", "at": now()})
                try:
                    # IDs remain bound to this native connection and request.
                    # Native ACKs run on the reader, never on this waiter.
                    if self.closed.is_set() or (path / "resolution.json").exists():
                        return
                    self.reply_context.path = path
                    try:
                        reply(value)
                    finally:
                        self.reply_context.path = None
                    with self.lock:
                        if not (path / "resolution.json").exists():
                            write_json(path / "resolution.json", {"state": "replied", "at": now()})
                    write_json(attempt, {"decision": value, "state": "replied", "at": now()})
                    return
                except Exception as error:
                    detail = {"error": str(error), "at": now(), "request_file": str(path / "request.json"),
                              "action": "Inspect the native request and logs; correct the answer if still pending, or recover the original conversation. Do not blindly repeat work or bypass permissions."}
                    if (path / "resolution.json").exists():
                        write_json(attempt, {"decision": value, "state": "settled_by_native", "diagnostic": str(error), "at": now()})
                        return
                    write_json(attempt, {"decision": value, "state": "reply_failed", **detail})
                    with ExitStack() as locks:
                        for name in ("decision.lock", "delivery.lock"):
                            while not self.closed.is_set():
                                try:
                                    locks.enter_context(platform.session_lease(path / name))
                                    break
                                except SwarmError as busy:
                                    if busy.code != "session_busy":
                                        raise
                                    self.closed.wait(0.01)
                            if self.closed.is_set():
                                return
                        with self.lock:
                            if (path / "resolution.json").exists():
                                return
                            write_json(path / "reply-error.json", detail)
                            for name in ("decision.json", "received.json", "delivery.json"):
                                (path / name).unlink(missing_ok=True)
                    self.issue("native_reply_failed", {"request_id": request_id, **detail})
                    # A corrected answer is explicit. Never auto-resend an
                    # uncertain native operation, and never kill other work.
                    notify()

        for action in (await_decision, notify):
            thread = threading.Thread(target=action, daemon=True)
            self.threads.append(thread)
            thread.start()
        return request_id

    def issue(self, code, detail):
        from .messages import issue
        # Keep diagnostics visible even when no host wake-up interface exists.
        write_json(self.directory / "communication-error.json", {"code": code, "detail": detail, "at": now()})
        if self.task.get("agent_id"):
            try:
                issue(self.records, self.task["agent_id"], None, code, detail)
            except (SwarmError, OSError):
                pass  # wait/inspect still expose the durable diagnostic.

    def unsupported(self, native_id, native_request):
        name = "native_" + uuid.uuid5(uuid.NAMESPACE_URL, str(native_id) + ":" + str(native_request)).hex
        path = self.directory / "native-issues" / (name + ".json")
        if path.exists():
            return
        detail = {"native_request_id": native_id, "native_request": native_request,
                  "native_session_id": read_json(self.directory / "handle.json", {}).get("native_session_id"),
                  "request_file": str(path), "action": "Inspect this request and native logs. Continue supported work; recover via native conversation or resume after the live turn ends. Do not fabricate a response or rerun side effects."}
        write_json(path, detail)
        self.issue("unsupported_native_interaction", detail)

    def withdraw(self, native_id, state="withdrawn", *, epoch=None):
        request_id = self.key(native_id, epoch)
        path = (self.native_requests.get(str(native_id)) if epoch is None else None) or location(self.records, self.task["call_id"], request_id)
        with self.lock:
            if (path / "request.json").exists() and not (path / "resolution.json").exists():
                write_json(path / "resolution.json", {"state": state, "at": now()})

    def close(self):
        self.closed.set()
        with self.lock:
            for path in (self.directory / "approvals").glob("approval_*"):
                if not (path / "resolution.json").exists():
                    write_json(path / "resolution.json", {"state": "cancelled" if (self.directory / "cancel.json").exists() else "interrupted", "at": now()})
