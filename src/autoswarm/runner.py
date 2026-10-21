"""One runner owns one invocation, its process, final result and native return."""
from __future__ import annotations

import hashlib
from contextlib import ExitStack
import os
import subprocess
import threading
import time

from . import platform
from .hosts import deliver
from .native import Native, Output
from .messages import Mailbox, finished, issue
from .providers import OWNED_SERVERS
from .records import Records, now, read_json, write_json


def execute(records: Records, call_id: str) -> int:
    directory, task = records.require(call_id)
    handle = {"pid": os.getpid(), "identity": platform.identity(os.getpid()), "started_at": now(), "native_session_id": task.get("native_session_id"), "phase": "startup"}
    write_json(directory / "handle.json", handle)
    result = {"call_id": call_id, "agent_id": task["agent_id"], "controller_id": task["controller_id"], "provider": task["provider"], "native_session_id": task.get("native_session_id"), "outcome": "failed", "content": "", "error": None, "artifacts": [], "finished_at": None}
    result["requested_selection"] = {key: task[key] for key in ("model", "effort") if task.get(key) is not None}
    process = None
    session_lock = None
    cancelled = False
    native = None
    mailbox = None
    handle_lock = threading.Lock()
    output = Output(session_id=task.get("native_session_id"))
    environment = ExitStack()
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
            from .selection import native_environment
            env = environment.enter_context(native_environment(task, env))
            with (directory / "stderr.log").open("wb") as stderr, (directory / "stdout.log").open("wb") as stdout_log:
                process = subprocess.Popen(task["command"], cwd=task["workspace"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, **platform.detached_options())
                handle["child_pid"] = process.pid
                write_json(directory / "handle.json", handle)
                failure = []

                def session_changed(value):
                    progress(handle["phase"] if task["provider"] == "opencode" else "execution", native_session_id=value)

                def progress(phase, **details):
                    with handle_lock:
                        value = {**handle, "phase": phase, **details}
                        if value != handle:
                            handle.update(value)
                            write_json(directory / "handle.json", handle)

                native = Native(records, task, process, output, session_changed, progress)
                mailbox = Mailbox(records, task, native)

                def read_output():
                    try:
                        for raw in process.stdout:
                            if task["provider"] == "opencode":
                                stdout_log.write(raw)
                                stdout_log.flush()
                            native.feed(raw.decode("utf-8", errors="replace"))
                        if task["provider"] != "opencode" and not output.terminal:
                            failure.append("Native output closed before a final result; inspect stderr.log")
                    except BaseException as error:
                        output.error_code = getattr(error, "code", "native_protocol_error")
                        failure.append(str(error))

                reader = threading.Thread(target=read_output)
                reader.start()
                native.start()
                closing = False
                while process.poll() is None:
                    if (directory / "cancel.json").exists() and not cancelled:
                        cancelled = True
                        platform.stop_child(process)
                    if failure or native.broker.errors:
                        failure.extend(native.broker.errors)
                        platform.stop_child(process)
                    if not cancelled and not failure and not closing:
                        at_boundary = native.turn_done.is_set()
                        try:
                            if native.adapter and hasattr(native.adapter, "poll"):
                                native.adapter.poll()
                            mailbox.poll()
                            at_boundary = native.turn_done.is_set()
                            complete = native.turn_done.is_set() and mailbox.boundary()
                        except Exception as error:
                            mailbox.failed(error)
                            complete = at_boundary or native.turn_done.is_set()
                            if complete and not output.terminal and not output.error:
                                output.error = "Could not continue the native conversation: " + str(error)
                        if complete:
                            closing = True
                            native.finished.set()
                            native.end_input()
                            if task["provider"] in OWNED_SERVERS:
                                platform.stop_child(process)
                    time.sleep(0.1)
                reader.join()
                if native.work:
                    native.work.join()
                failure.extend(native.broker.errors)
                process.stdout.close()
                preserved_original = False
                if not cancelled and mailbox.saved is not None and (failure or output.error or not output.terminal):
                    # The task already produced a successful native result.
                    # A later communication turn must not erase that result.
                    error = output.error or (failure[0] if failure else "Native communication turn did not complete")
                    mailbox.failed(error)
                    write_json(directory / "communication-error.json", {"error": error, "at": now()})
                    failure.clear()
                    preserved_original = True
                result["exit_code"] = process.returncode
                success_exit = preserved_original or process.returncode == 0 or (task["provider"] in OWNED_SERVERS and output.terminal)
                result["outcome"] = "cancelled" if cancelled or output.cancelled else ("completed" if success_exit and not output.error and not failure and output.terminal else "failed")
                result["error"] = output.error or (failure[0] if failure else None)
                if output.error_code:
                    result["error_code"] = output.error_code
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
        environment.close()
        if session_lock:
            session_lock.close()
        result["finished_at"] = now()
        if result.get("error"):
            result["diagnostics"] = {"phase": handle.get("phase"), "command": task["command"], "workspace": task["workspace"], "directory": str(directory)}
        write_json(directory / "result.json", result)
    try:
        if result["outcome"] == "failed":
            if native:
                from .messages import location
                for message in [*native.initial_messages, *(mailbox.active_messages if mailbox else [])]:
                    state = read_json(location(records, message["message_id"]) / "delivery.json", {}).get("state")
                    if state != "delivered":
                        issue(records, task["agent_id"], message, "native_message_failed", result.get("error") or "Native call failed while handling this message")
            issue(records, task["agent_id"], None, result.get("error_code", "child_failed"), result.get("error") or "Child failed; inspect its retained call records")
        finished(records, task)
    except Exception as error:
        # Even a record/delivery failure must not hide the completed call.
        write_json(directory / "communication-error.json", {"error": str(error), "at": now()})
    receipt = deliver(records, call_id)
    # Connection loss is recoverable. No retry count or task deadline is added.
    # Permanent binding/protocol errors remain explicit and can be redelivered.
    while receipt.get("retryable") and not (directory / "received.json").exists() and not (directory / "cancel.json").exists():
        time.sleep(0.5)
        receipt = deliver(records, call_id)
    return 0
