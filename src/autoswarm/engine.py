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
from . import approvals
from .hosts import detect_parent
from .providers import canonical, invocation
from .records import Records, SwarmError, now, read_json, write_json


def runtime_argv() -> list[str]:
    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "autoswarm"]


def runtime_env(records: Records) -> dict:
    env = dict(os.environ)
    env["AUTOSWARM_HOME"] = str(records.home)
    if getattr(sys, "frozen", False):
        # A detached onefile runner must own its extraction directory. Reusing
        # the CLI parent's _MEIPASS works until that short-lived CLI exits and
        # its bootloader deletes libraries needed later by the child.
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    else:
        root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def start_call(records: Records, provider: str, prompt: str, workspace: str | Path, *, parent: dict | None = None, native_session_id: str | None = None, model: str | None = None, provider_args: list[str] | None = None, previous_call: str | None = None, return_mode: str = "native", delegate_approvals: bool | None = None) -> dict:
    provider = canonical(provider)
    directory_path = Path(workspace).expanduser().resolve()
    if not directory_path.is_dir():
        raise SwarmError("workspace_not_found", f"Workspace is not a directory: {directory_path}")
    call_id = "call_" + uuid.uuid4().hex
    task = {"call_id": call_id, "provider": provider, "prompt": prompt, "workspace": str(directory_path), "parent": parent, "native_session_id": native_session_id, "model": model, "provider_args": provider_args or [], "previous_call": previous_call, "return_mode": return_mode, "created_at": now()}
    task["delegate_approvals"] = delegate_approvals
    approvals.delegated(records, task)
    # Validate and resolve the executable before accepting a background call.
    task["command"] = invocation(task, records.config())
    directory = records.call(call_id)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_json(directory / "task.json", task)
    with (directory / "runner.log").open("ab") as log:
        process = subprocess.Popen([*runtime_argv(), "_runner", call_id], env=runtime_env(records), stdin=subprocess.DEVNULL, stdout=log, stderr=log, **platform.detached_options())
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
    return {"call_id": call_id, "provider": task["provider"], "state": state, "return_mode": task.get("return_mode", "native"), "native_session_id": (result or {}).get("native_session_id") or (handle or {}).get("native_session_id") or task.get("native_session_id"), "workspace": task["workspace"], "directory": str(directory), "parent": task.get("parent"), "result": result, "delivery": read_json(directory / "delivery.json"), "handle": handle, "delegate_approvals": approvals.delegated(records, task), "approvals": approvals.pending(records, call_id) if state == "running" else []}


def receive(records: Records, call_ids: list[str], *, mark_received: bool = True, unseen: bool = False) -> list[dict]:
    output = []
    for call_id in call_ids:
        directory, _ = records.require(call_id)
        info = inspect_call(records, call_id)
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
    return output


def wait(records: Records, call_ids: list[str], *, any_result: bool = False, mark_received: bool = True, unseen: bool = False) -> list[dict]:
    for call_id in call_ids:
        records.require(call_id)
    while True:
        values = receive(records, call_ids, mark_received=False, unseen=unseen)
        if any(value.get("kind") == "approval_required" for value in values) or len(values) == len(call_ids) or (any_result and values):
            return receive(records, list(dict.fromkeys(value["call_id"] for value in values)), mark_received=mark_received, unseen=unseen)
        time.sleep(0.1)


def resume(records: Records, call_id: str, prompt: str, *, parent: dict | None = None, inherit_parent: bool = True, model: str | None = None, provider_args: list[str] | None = None, return_mode: str = "native", delegate_approvals: bool | None = None) -> dict:
    directory, task = records.require(call_id)
    info = inspect_call(records, call_id)
    if info["state"] == "running":
        raise SwarmError("session_busy", "The original child is still running; use wait or cancel before resuming")
    session = info["native_session_id"]
    if not session:
        raise SwarmError("session_unavailable", "Provider did not return a native session ID")
    # A result is retained in the old directory; continuing creates a new call.
    return start_call(records, task["provider"], prompt, task["workspace"], parent=parent if parent is not None or not inherit_parent else task.get("parent"), native_session_id=session, model=model if model is not None else task.get("model"), provider_args=provider_args if provider_args is not None else task.get("provider_args"), previous_call=call_id, return_mode=return_mode, delegate_approvals=delegate_approvals if delegate_approvals is not None else task.get("delegate_approvals"))


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
