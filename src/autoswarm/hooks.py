"""Claude lifecycle integration, including native asyncRewake for idle sessions."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
import time

from .engine import inspect_call
from . import approvals
from .hosts import result_message
from .platform import session_lease
from .records import Records, SwarmError, now, read_json, write_json


def pending_calls(records: Records, session_id: str, *, include_submitted: bool = False):
    for directory, task in records.calls():
        parent = task.get("parent") or {}
        if parent.get("host") != "claude-code" or parent.get("session_id") != session_id:
            continue
        if task.get("return_mode") == "wait" or (directory / "received.json").exists():
            continue
        receipt = read_json(directory / "delivery.json", {})
        if not include_submitted and receipt.get("state") in {"submitted", "delivered"}:
            continue
        yield directory, task


def handle_hook(records: Records, event: str) -> int:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    if not session_id:
        return 0
    if event == "session-start":
        parent = {"host": "claude-code", "session_id": session_id, "mode": "hook"}
        path = records.home / "parents" / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")
        write_json(path, parent)
        env_file = os.environ.get("CLAUDE_ENV_FILE")
        if env_file and os.name != "nt":
            with open(env_file, "a", encoding="utf-8") as stream:
                stream.write("\nexport AUTOSWARM_PARENT=" + shlex.quote(json.dumps(parent)) + "\n")
                stream.write("export AUTOSWARM_HOME=" + shlex.quote(str(records.home)) + "\n")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": f"AutoSwarm original-session binding: {path}. For AutoSwarm run/resume commands pass --parent {json.dumps(str(path))} if AUTOSWARM_PARENT is not available. This binding belongs only to this session."}}))
        return 0
    if event == "stop":
        calls = list(pending_calls(records, session_id, include_submitted=True))
        if not calls:
            return 0
        ready, running, waiting_for_input = [], [], False
        for directory, task in calls:
            info = inspect_call(records, task["call_id"])
            if info["result"]:
                ready.append(result_message(info["result"], directory))
                write_json(directory / "received.json", {"at": now(), "mechanism": "claude_stop_hook"})
            elif info["state"] == "interrupted":
                ready.append(f"AutoSwarm call {task['call_id']} was interrupted. Inspect it and decide whether to resume.")
                write_json(directory / "received.json", {"at": now(), "mechanism": "claude_stop_hook"})
            else:
                requests = approvals.pending(records, task["call_id"])
                if requests:
                    waiting_for_input = True
                    for request in requests:
                        path = approvals.location(records, task["call_id"], request["request_id"])
                        if not (path / "received.json").exists():
                            ready.append(approvals.message(request))
                            write_json(path / "received.json", {"at": now(), "mechanism": "claude_stop_hook"})
                else:
                    running.append(task["call_id"])
        reason = "\n\n".join(ready)
        # Once the request has reached the model, permit the turn to end so
        # a human can answer. Waiting for a paused child would deadlock.
        if waiting_for_input and not ready:
            return 0
        if running and not waiting_for_input:
            reason += "\nAutoSwarm child calls are still executing. Use AutoSwarm wait to collect the required results before final delivery: " + " ".join(running)
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    key = hashlib.sha256(session_id.encode()).hexdigest()
    try:
        lease = session_lease(records.home / "hook-watchers" / key)
    except SwarmError:
        return 0
    try:
        while True:
            calls = list(pending_calls(records, session_id))
            if not calls:
                return 0
            for directory, task in calls:
                info = inspect_call(records, task["call_id"])
                requests = approvals.pending(records, task["call_id"], unseen=True) if info["state"] == "running" else []
                if requests:
                    request = requests[0]
                    path = approvals.location(records, task["call_id"], request["request_id"])
                    write_json(path / "delivery.json", {"call_id": task["call_id"], "request_id": request["request_id"], "state": "submitted", "continued": False, "mechanism": "claude_async_rewake", "at": now()})
                    print(approvals.message(request), file=sys.stderr, flush=True)
                    return 2
                if info["result"] is None and info["state"] != "interrupted":
                    continue
                message = result_message(info["result"], directory) if info["result"] else f"AutoSwarm {task['call_id']} was interrupted; inspect the call."
                write_json(directory / "delivery.json", {"call_id": task["call_id"], "state": "submitted", "continued": False, "mechanism": "claude_async_rewake", "at": now()})
                print(message, file=sys.stderr, flush=True)
                # Claude's asyncRewake contract: exit 2 wakes an idle session.
                # Submission is not mislabeled as an observed assistant turn.
                return 2
            time.sleep(0.1)
    finally:
        lease.close()
