"""One runner owns one invocation, its process, final result and native return."""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time

from . import platform
from .hosts import deliver
from .native import Native, Output
from .records import Records, now, write_json


def execute(records: Records, call_id: str) -> int:
    directory, task = records.require(call_id)
    handle = {"pid": os.getpid(), "identity": platform.identity(os.getpid()), "started_at": now(), "native_session_id": task.get("native_session_id")}
    write_json(directory / "handle.json", handle)
    result = {"call_id": call_id, "provider": task["provider"], "native_session_id": task.get("native_session_id"), "outcome": "failed", "content": "", "error": None, "artifacts": [], "finished_at": None}
    process = None
    session_lock = None
    cancelled = False
    native = None
    output = Output(session_id=task.get("native_session_id"))
    try:
        if (directory / "cancel.json").exists():
            cancelled = True
        else:
            if task.get("native_session_id"):
                key = hashlib.sha256((task["provider"] + ":" + task["native_session_id"]).encode()).hexdigest()
                path = records.home / "session-locks" / key
                session_lock = platform.session_lease(path)
            env = dict(os.environ)
            # Existing credentials/config stay native. Caller identity must not
            # be inherited as the identity of a newly created child.
            for name in ("AUTOSWARM_PARENT", "CODEX_THREAD_ID", "CLAUDE_SESSION_ID", "CLAUDECODE"):
                env.pop(name, None)
            env["AUTOSWARM_CALL_ID"] = call_id
            env["AUTOSWARM_HOME"] = str(records.home)
            with (directory / "stderr.log").open("wb") as stderr:
                process = subprocess.Popen(task["command"], cwd=task["workspace"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, **platform.detached_options())
                handle["child_pid"] = process.pid
                write_json(directory / "handle.json", handle)
                failure = []

                def session_changed(value):
                    handle["native_session_id"] = value
                    write_json(directory / "handle.json", handle)

                native = Native(records, task, process, output, session_changed)

                def read_output():
                    try:
                        for raw in process.stdout:
                            native.feed(raw.decode("utf-8", errors="replace"))
                    except BaseException as error:
                        failure.append(str(error))

                reader = threading.Thread(target=read_output)
                reader.start()
                native.start()
                while process.poll() is None:
                    if (directory / "cancel.json").exists() and not cancelled:
                        cancelled = True
                        platform.stop_child(process)
                    if failure or native.broker.errors:
                        failure.extend(native.broker.errors)
                        platform.stop_child(process)
                    time.sleep(0.1)
                reader.join()
                if native.work:
                    native.work.join()
                failure.extend(native.broker.errors)
                process.stdout.close()
                result["exit_code"] = process.returncode
                success_exit = process.returncode == 0 or (task["provider"] == "opencode" and output.terminal)
                result["outcome"] = "cancelled" if cancelled or output.cancelled else ("completed" if success_exit and not output.error and not failure and output.terminal else "failed")
                result["error"] = output.error or (failure[0] if failure else None)
                if result["outcome"] == "failed" and not result["error"]:
                    result["error"] = f"Provider exited with code {process.returncode}" if process.returncode else "Provider returned no recognized final event"
                    if output.finish_reason:
                        result["error"] += f" (last native finish reason: {output.finish_reason}; see stderr.log)"
                if output.finish_reason:
                    result["finish_reason"] = output.finish_reason
                if output.permission_denials:
                    result["permission_denials"] = output.permission_denials
        if cancelled:
            result["outcome"] = "cancelled"
        result["content"] = output.content
        result["native_session_id"] = output.session_id
    except BaseException as error:
        result["error"] = str(error)
        result["error_code"] = getattr(error, "code", "execution_failed")
        result["content"] = output.content
        result["native_session_id"] = output.session_id
        if process and process.poll() is None:
            platform.stop_child(process)
            process.wait()
    finally:
        if native:
            native.close()
        if session_lock:
            session_lock.close()
        result["finished_at"] = now()
        write_json(directory / "result.json", result)
    receipt = deliver(records, call_id)
    # Connection loss is recoverable. No retry count or task deadline is added.
    # Permanent binding/protocol errors remain explicit and can be redelivered.
    while receipt.get("retryable") and not (directory / "received.json").exists() and not (directory / "cancel.json").exists():
        time.sleep(0.5)
        receipt = deliver(records, call_id)
    return 0
