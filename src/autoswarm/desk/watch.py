"""Read-only JSONL feed for Desk: python -m autoswarm.desk.watch [--home PATH].

No cursor or history is persisted. Windows directory notifications wake the
scanner; a periodic scan also detects leases released without a file write.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import re
import sys
import time
from pathlib import Path

from autoswarm import platform
from autoswarm.records import data_home, read_json
from autoswarm.capture import settings
from . import native_calls
from .streams import page


def runner_alive(directory: Path, handle: dict) -> bool:
    """Probe an EXISTING lease without creating files (platform.running does).

    A shared nonblocking read lock conflicts with the runner's exclusive lock.
    Keep the same legacy PID/identity fallback as AutoSwarm's inspector.
    """
    lock = directory / "runner.lock"
    try:
        with lock.open("rb") as stream:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBRLCK, 1)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    fcntl.flock(stream, fcntl.LOCK_UN)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    return True
                raise
    except FileNotFoundError:
        pass
    return bool(handle.get("identity") and handle.get("pid") and
                platform.identity(handle["pid"]) == handle["identity"])


class Scanner:
    def __init__(self, home):
        self.home = data_home(home)
        self.warnings = []
        self.streams = {}
        self.native = {}

    def load(self, path):
        try:
            value = read_json(path)
            if value is not None and not isinstance(value, dict):
                raise ValueError("Expected a JSON object")
            return value
        except (OSError, ValueError) as error:
            self.warnings.append({"path": str(path), "message": str(error)})
            return None

    def scan(self):
        self.warnings = []
        config = self.load(self.home / "config.json") or {}
        agents, calls, requests, messages = {}, {}, {}, {}
        for path in sorted((self.home / "agents").glob("*/agent.json")):
            record = self.load(path)
            if record:
                agents[path.parent.name] = record
        for directory in sorted((self.home / "calls").glob("call_*")):
            task = self.load(directory / "task.json")
            if not task:
                continue
            cid = directory.name
            handle = self.load(directory / "handle.json") or {}
            result = self.load(directory / "result.json")
            try:
                alive = result is None and runner_alive(directory, handle)
            except OSError as error:
                alive = False
                self.warnings.append({"path": str(directory / "runner.lock"), "message": str(error)})
            # The runner may publish between reading result and checking its lease.
            if result is None and not alive:
                result = self.load(directory / "result.json")
            state = (result.get("outcome", "failed") if result is not None else
                     "running" if alive else "interrupted")
            calls[cid] = {"id": cid, "agent_id": task.get("agent_id", cid),
                          "task": task, "handle": handle, "result": result,
                          "state": state, "directory": str(directory)}
            event_path, name = directory / "events.jsonl", "events.jsonl"
            # A running Claude Code / Codex / Cursor call streams into a temporary live.jsonl.
            if result is None and not event_path.exists() and (directory / "live.jsonl").exists():
                event_path, name = directory / "live.jsonl", "live.jsonl"
            if alive or cid in self.streams:
                try:
                    stat = event_path.stat()
                    signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                except OSError:
                    signature = None
                old = self.streams.get(cid)
                if old is None or old[0] != signature:
                    self.streams[cid] = (signature, {**page(directory, task.get("provider"), handle.get("native_session_id") or task.get("native_session_id"), name=name),
                                                     **({"source": "live"} if name == "live.jsonl" else {})})
                calls[cid]["stream"] = self.streams[cid][1]
            else:
                calls[cid]["stream"] = {"available": event_path.is_file(), "lazy": True}
            approval_config = config.get("approvals") or {}
            if not isinstance(approval_config, dict):
                approval_config = {}
            delegated = task.get("delegate_approvals")
            if delegated is None:
                delegated = approval_config.get("delegate_to_parent", True)
            for path in sorted((directory / "approvals").glob("*/request.json")):
                request = self.load(path)
                if not request:
                    continue
                decision = self.load(path.parent / "decision.json")
                resolution = self.load(path.parent / "resolution.json")
                rid = path.parent.name
                requests[cid + "/" + rid] = {
                    "id": rid, "call_id": cid, "request": request,
                    "decision": decision, "resolution": resolution,
                    "delegate_to_parent": delegated,
                    "pending": bool(alive and not decision and not resolution and
                                    not (directory / "cancel.json").exists())}
        for path in sorted((self.home / "agents").glob("*/messages/*/message.json")):
            message = self.load(path)
            if message:
                mid = message.get("message_id", path.parent.name)
                messages[mid] = {"id": mid, "message": message,
                                 "delivery": self.load(path.parent / "delivery.json")}
        self.native_streams(calls)
        from .native_history import bound_conversations
        native = bound_conversations(agents)
        return {"home": str(self.home), "exists": self.home.is_dir(), "agents": agents, "native_conversations": native,
                "calls": calls, "requests": requests, "messages": messages,
                "warnings": self.warnings, "capture": settings(config)["capture"], "events": settings(config)}

    def native_streams(self, calls):
        """Claude Code, Codex and Cursor calls show their agent's own session record.
        While running, the temporary live stream shows text as it is typed (the
        record is re-read only if there is none); a finished call is read from
        the record when opened. The captured copy remains the fallback."""
        for cid, call in calls.items():
            if not native_calls.applies(call):
                continue
            running, old = call["state"] == "running", self.native.get(cid)
            if running and call["stream"].get("source") == "live" and call["stream"].get("available"):
                continue
            if running or (old and not old[2]):
                # Re-read while running; one last read after it ends, then the page is settled.
                mark = native_calls.signature(cid, calls)
                if old is None or old[0] != mark or not running:
                    old = self.native[cid] = (mark, native_calls.page(cid, calls), not running)
            if old:
                # The agent's record wins; without it, the copy; without both, why it is gone.
                if old[1] and (old[1].get("available") or not call["stream"].get("available")):
                    call["stream"] = old[1]
            elif not call["stream"].get("available") or call["stream"].get("lazy"):
                call["stream"] = {"available": True, "lazy": True, "source": "native"}


class Changes:
    """Recursive Windows change notification, with portable timed fallback."""
    def __init__(self, home):
        self.handle = None
        if os.name == "nt" and home.is_dir():
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
            kernel.FindFirstChangeNotificationW.restype = wintypes.HANDLE
            kernel.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
            kernel.FindNextChangeNotification.restype = wintypes.BOOL
            kernel.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            handle = kernel.FindFirstChangeNotificationW(str(home), True, 0x01 | 0x02 | 0x08 | 0x10)
            if handle != ctypes.c_void_p(-1).value:
                self.handle, self.kernel = handle, kernel

    def wait(self, interval):
        if self.handle is None:
            time.sleep(interval)
        elif self.kernel.WaitForSingleObject(self.handle, int(interval * 1000)) == 0:
            self.kernel.FindNextChangeNotification(self.handle)
            time.sleep(0.08)  # Coalesce atomic replace bursts.

    def close(self):
        if self.handle is not None:
            self.kernel.FindCloseChangeNotification(self.handle)


def diff(previous, current):
    events = []
    for collection, event in (("agents", "agent.changed"), ("calls", "call.changed"),
                              ("requests", "approval.changed"), ("messages", "message.changed")):
        before, after = previous[collection], current[collection]
        for key, value in after.items():
            if before.get(key) != value:
                events.append({"event": event, "id": key, "value": value})
        for key in before.keys() - after.keys():
            events.append({"event": collection + ".removed", "id": key})
    return events


def watch(home=None, once=False, interval=1.0):
    scanner = Scanner(home)
    changes = Changes(scanner.home)
    previous = None
    try:
        while True:
            current = scanner.scan()
            if current != previous:
                # Atomic frames prevent impossible intermediate UI trees.
                value = {"event": "snapshot", "data": current,
                         "changes": [] if previous is None else diff(previous, current)}
                print(json.dumps(value, ensure_ascii=False), flush=True)
                previous = current
            if once:
                return
            changes.wait(interval)
    finally:
        changes.close()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--events", help="Read one call's event page on demand")
    parser.add_argument("--before", type=int)
    parser.add_argument("--native", help="Read one bound agent's native turns on demand")
    args = parser.parse_args()
    try:
        if args.native:
            from .native_history import agent_conversation
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", args.native):
                raise SystemExit("invalid agent id")
            print(json.dumps(agent_conversation(data_home(args.home), args.native), ensure_ascii=False))
        elif args.events:
            from autoswarm.records import Records
            records = Records(args.home)
            directory, task = records.require(args.events)
            handle = read_json(directory / "handle.json", {})
            # The agent's own record first (whole call, no earlier pages); the captured copy as fallback.
            value = native_calls.page(args.events, native_calls.load_calls(records.home)) if args.before is None else None
            if not value or not value.get("available"):
                captured = page(directory, task.get("provider"), handle.get("native_session_id") or task.get("native_session_id"), max(0, args.before) if args.before is not None else None)
                value = captured if captured.get("available") or not value else {**captured, "missing": value.get("missing")}
            print(json.dumps(value, ensure_ascii=False))
        else:
            watch(args.home, args.once, max(0.1, args.interval))
    except (KeyboardInterrupt, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
