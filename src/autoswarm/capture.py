"""Optional best-effort native event tee; never changes native execution.

Desk reads Claude Code, Codex and Cursor calls from those agents' own session
records, so it sets events.capture to "auto": only other agents keep their
native output in calls/<id>/events.jsonl, which stays with its call directory.
The three read natively write a temporary live.jsonl while they run (the text
as it is typed) that is removed when the call ends. events.max_call_mb caps
one call's file so a runaway terminal stream cannot fill the disk.
"""
from __future__ import annotations

import json
import math
import threading
import time

from . import platform
from .records import Records, SwarmError, now, write_json

MB = 1024 * 1024
DEFAULT_CALL_MB = 64
MAX_CALL_MB = 1048576
# Agents whose own session records Desk reads; capture "auto" skips them.
NATIVE_RECORDS = ("claude-code", "codex", "cursor")
CAPTURE = (True, False, "auto")


def _megabytes(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= MAX_CALL_MB:
        raise SwarmError("invalid_config", f"events.max_call_mb must be a number from 0 to {MAX_CALL_MB} (0 = no limit)")
    return int(value) if float(value).is_integer() else value


def settings(config) -> dict:
    """Effective event settings. A malformed value falls back to its default:
    a typo must never fail a native call."""
    events = config.get("events", {}) if isinstance(config, dict) else {}
    events = events if isinstance(events, dict) else {}
    try:
        limit = _megabytes(events.get("max_call_mb", DEFAULT_CALL_MB))
    except SwarmError:
        limit = DEFAULT_CALL_MB
    return {"capture": events.get("capture") if events.get("capture") in ("auto", True) else False, "max_call_mb": limit}


def captures(config, provider) -> bool:
    """Whether a call of `provider` keeps its native events in events.jsonl."""
    capture = settings(config)["capture"]
    return capture is True or (capture == "auto" and provider not in NATIVE_RECORDS)


def configure(records: Records, *, capture=None, max_call_mb=None) -> dict:
    """Persist event settings; None leaves a value unchanged. Values are
    validated before the config file is written; other fields are kept."""
    values = {}
    if max_call_mb is not None:
        values["max_call_mb"] = _megabytes(max_call_mb)
    if capture is not None:
        if capture not in CAPTURE:
            raise SwarmError("invalid_config", "events.capture must be true, false or \"auto\"")
        values["capture"] = capture
    if values:
        with platform.session_lease(records.home / "config.lock"):
            config = records.config() or {}
            if not isinstance(config.get("events", {}), dict):
                raise SwarmError("invalid_config", "events must be a JSON object")
            config.setdefault("events", {}).update(values)
            write_json(records.home / "config.json", config)
    return settings(records.config())


def live(config, provider) -> bool:
    """Whether a call keeps a temporary live.jsonl while it runs: agents Desk
    reads from their own records write those records whole, so the text as it
    is typed only exists in the protocol stream. The file is removed when the
    call ends; the native record is the history."""
    return settings(config)["capture"] == "auto" and provider in NATIVE_RECORDS


class Capture:
    """Appends native output lines to events.jsonl when this call is captured,
    or to a temporary live.jsonl that is removed when the call ends.

    The line that would cross events.max_call_mb is replaced by a single
    {"autoswarm": "capture_truncated"} marker and capture stops for this call.
    """
    def __init__(self, directory, config, provider=None):
        self.stream = None
        self.lock = threading.Lock()
        self.limit = None
        self.written = 0
        self.at_line_start = True
        self.temporary = None
        name = "events.jsonl" if captures(config, provider) else "live.jsonl" if live(config, provider) else None
        if name:
            mb = settings(config)["max_call_mb"]
            self.limit = int(mb * MB) if mb else None
            try:
                self.stream = (directory / name).open("ab")
                self.written = self.stream.tell()
                self.temporary = directory / name if name == "live.jsonl" else None
            except OSError:
                pass

    def mark(self, kind):
        """An AutoSwarm line in the stream, e.g. "prompt" where this call's own
        prompt starts (an ACP session/load replays the old transcript first)."""
        self.write(json.dumps({"autoswarm": kind, "at": now()}).encode("utf-8") + b"\n")

    def write(self, raw):
        with self.lock:
            if self.stream is not None:
                try:
                    if self.limit is not None and self.written + len(raw) > self.limit:
                        marker = json.dumps({"autoswarm": "capture_truncated", "limit_bytes": self.limit, "at": now()}).encode("utf-8")
                        self.stream.write((b"" if self.at_line_start else b"\n") + marker + b"\n")
                        self._close()
                        return
                    self.stream.write(raw)
                    self.stream.flush()
                    self.written += len(raw)
                    if raw:
                        self.at_line_start = raw.endswith(b"\n")
                except (OSError, ValueError):
                    self._close()

    def _close(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def close(self):
        with self.lock:
            self._close()
            if self.temporary is not None:
                # A viewer may have the file open for a moment (Windows refuses
                # to delete it then); a leftover is ignored once the call returned.
                for _ in range(10):
                    try:
                        self.temporary.unlink(missing_ok=True)
                        break
                    except OSError:
                        time.sleep(0.1)
                self.temporary = None
