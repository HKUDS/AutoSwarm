"""Call orchestration: independent runners and files, no central service."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import platform
from . import approvals, messages
from .hosts import detect_parent
from .providers import canonical, invocation
from .records import Records, SwarmError, now, read_json, write_json


def runtime_argv() -> list[str]:
    return [sys.executable, "-m", "autoswarm"]


def runtime_env(records: Records) -> dict:
    env = dict(os.environ)
    env["AUTOSWARM_HOME"] = str(records.home)
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def start_call(records: Records, provider: str, prompt: str, workspace: str | Path, *, parent: dict | None = None, native_session_id: str | None = None, model: str | None = None, effort: str | None = None, provider_args: list[str] | None = None, previous_call: str | None = None, return_mode: str = "native", delegate_approvals: bool | None = None, controller: str | None = None, name: str | None = None, peers: list[str] | None = None, rules: str | None = None, agent_id: str | None = None, message_only: bool = False, _dispatch_locked: bool = False) -> dict:
    provider = canonical(provider)
    directory_path = Path(workspace).expanduser().resolve()
    if not directory_path.is_dir():
        raise SwarmError("workspace_not_found", f"Workspace is not a directory: {directory_path}")
    call_id = "call_" + uuid.uuid4().hex
    task = {"call_id": call_id, "provider": provider, "prompt": prompt, "workspace": str(directory_path), "parent": parent, "native_session_id": native_session_id, "model": model, "provider_args": provider_args or [], "previous_call": previous_call, "return_mode": return_mode, "created_at": now()}
    task["effort"] = effort
    task["delegate_approvals"] = delegate_approvals
    approvals.delegated(records, task)
    # Validate and resolve the executable before accepting a background call.
    task["command"] = invocation(task, records.config())
    task["message_only"] = message_only
    value = messages.prepare(records, task, controller=controller, name=name, peers=peers, rules=rules, agent_id=agent_id)
    lease = None if _dispatch_locked else platform.session_lease(messages.agent_path(records, task["agent_id"]) / "dispatch.lock")
    directory = records.call(call_id)
    try:
        if agent_id:
            latest = messages.agent(records, agent_id)
            latest_directory, _ = records.require(latest["call_id"])
            if not latest.get("idle") and platform.running(read_json(latest_directory / "handle.json")):
                raise SwarmError("session_busy", "This conversation is handling work or messages; send to its stable agent_id or wait before resuming")
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        write_json(directory / "task.json", task)
        write_json(messages.agent_path(records, task["agent_id"]) / "agent.json", {**value, "idle": False})
        with (directory / "runner.log").open("ab") as log:
            process = subprocess.Popen([*runtime_argv(), "_runner", call_id], env=runtime_env(records), stdin=subprocess.DEVNULL, stdout=log, stderr=log, **platform.detached_options())
    finally:
        if lease:
            lease.close()
    while not (directory / "handle.json").exists() and not (directory / "result.json").exists():
        if process.poll() is not None:
            raise SwarmError("runner_start_failed", f"Runner exited before startup; see {directory / 'runner.log'}")
        time.sleep(0.02)
    threading.Thread(target=process.wait, daemon=True).start()
    return inspect_call(records, call_id)


def inspect_call(records: Records, call_id: str) -> dict:
    directory, task = records.require(call_id)
    handle = read_json(directory / "handle.json")
    result = read_json(directory / "result.json")
    if result is not None:
        state = "returned"
    elif platform.running(handle):
        state = "running"
    else:
        state = "interrupted"
    selection = {key: task[key] for key in ("model", "effort") if task.get(key) is not None}
    return {"call_id": call_id, "agent_id": task.get("agent_id", call_id), "controller_id": task.get("controller_id"), "communication": task.get("communication"), "provider": task["provider"], "state": state, "return_mode": task.get("return_mode", "native"), "native_session_id": (result or {}).get("native_session_id") or (handle or {}).get("native_session_id") or task.get("native_session_id"), "requested_selection": selection, "workspace": task["workspace"], "directory": str(directory), "parent": task.get("parent"), "result": result, "delivery": read_json(directory / "delivery.json"), "handle": handle, "communication_error": read_json(directory / "communication-error.json"), "delegate_approvals": approvals.delegated(records, task), "approvals": approvals.pending(records, call_id) if state == "running" else []}


def receive(records: Records, call_ids: list[str], *, mark_received: bool = True, unseen: bool = False, controller: str | None = None, include_inbox: bool = True) -> list[dict]:
    output = []
    for call_id in call_ids:
        directory, _ = records.require(call_id)
        info = inspect_call(records, call_id)
        diagnostic = info["communication_error"]
        if diagnostic and read_json(directory / "communication-received.json", {}).get("diagnostic_at") != diagnostic.get("at"):
            output.append({"kind": "communication_issue", "call_id": call_id, "agent_id": info["agent_id"], "diagnostic": diagnostic, "directory": str(directory)})
            if mark_received:
                write_json(directory / "communication-received.json", {"diagnostic_at": diagnostic.get("at"), "at": now()})
        if info["result"] is not None:
            output.append(info["result"])
            if mark_received:
                # Means read by the tool, not model approval or task acceptance.
                write_json(directory / "received.json", {"call_id": call_id, "at": now()})
        elif info["state"] == "interrupted":
            output.append({"call_id": call_id, "provider": info["provider"], "outcome": "interrupted", "native_session_id": info["native_session_id"], "content": "", "error": "Runner is no longer present and no final result was published", "artifacts": []})
        else:
            for request in approvals.pending(records, call_id, unseen=unseen):
                output.append(request)
                if mark_received:
                    write_json(approvals.location(records, call_id, request["request_id"]) / "received.json", {"at": now()})
    if not include_inbox:
        return output
    try:
        caller = messages.identity(records, controller)
    except SwarmError:
        caller = None
    if caller:
        output.extend(messages.receive(records, caller, mark=mark_received))
        # Mail can resume an idle child into a new call. Its native approvals
        # still belong to this parent even when it waits on older call IDs.
        for _, task in records.calls():
            if task.get("controller_id") == caller and task["call_id"] not in call_ids:
                for request in approvals.pending(records, task["call_id"], unseen=unseen):
                    output.append(request)
                    if mark_received:
                        write_json(approvals.location(records, task["call_id"], request["request_id"]) / "received.json", {"at": now()})
    return output


def wait(records: Records, call_ids: list[str], *, any_result: bool = False, mark_received: bool = True, unseen: bool = False, controller: str | None = None, include_inbox: bool = True) -> list[dict]:
    for call_id in call_ids:
        records.require(call_id)
    while True:
        values = receive(records, call_ids, mark_received=False, unseen=unseen, controller=controller, include_inbox=include_inbox)
        events = any(value.get("kind") in {"approval_required", "interaction_required", "agent_message", "communication_issue"} for value in values)
        complete = {value.get("call_id") for value in values if value.get("outcome")}
        if events or (call_ids and complete.issuperset(call_ids)) or (any_result and values):
            return receive(records, call_ids, mark_received=mark_received, unseen=unseen, controller=controller, include_inbox=include_inbox)
        time.sleep(0.1)


def resume(records: Records, call_id: str, prompt: str, *, parent: dict | None = None, inherit_parent: bool = True, model: str | None = None, effort: str | None = None, provider_args: list[str] | None = None, return_mode: str = "native", delegate_approvals: bool | None = None) -> dict:
    directory, task = records.require(call_id)
    info = inspect_call(records, call_id)
    if info["state"] == "running":
        raise SwarmError("session_busy", "The original child is still running; use wait or cancel before resuming")
    session = info["native_session_id"]
    if not session:
        raise SwarmError("session_unavailable", "Provider did not return a native session ID")
    # A result is retained in the old directory; continuing creates a new call.
    return start_call(records, task["provider"], prompt, task["workspace"], parent=parent if parent is not None or not inherit_parent else task.get("parent"), native_session_id=session, model=model if model is not None else task.get("model"), effort=effort if effort is not None else task.get("effort"), provider_args=provider_args if provider_args is not None else task.get("provider_args"), previous_call=call_id, return_mode=return_mode, delegate_approvals=delegate_approvals if delegate_approvals is not None else task.get("delegate_approvals"), agent_id=task.get("agent_id", call_id))


def cancel(records: Records, call_id: str) -> dict:
    directory, _ = records.require(call_id)
    info = inspect_call(records, call_id)
    if platform.running(info["handle"]):
        write_json(directory / "cancel.json", {"call_id": call_id, "at": now()})
        return {"call_id": call_id, "cancel_requested": True, "result": info["result"]}
    return {"call_id": call_id, "cancel_requested": False, "state": info["state"], "result": info["result"]}


def parent_from_options(parent_file: str | None, no_parent: bool = False) -> dict | None:
    if no_parent:
        return None
    if parent_file:
        parent = read_json(Path(parent_file))
        if not isinstance(parent, dict) or not parent.get("session_id"):
            raise SwarmError("invalid_parent", "Parent file must contain host and session_id")
        return parent
    return detect_parent()
