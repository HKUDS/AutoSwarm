"""Small, independently written call receipts; native agents own their histories."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SwarmError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_home(value: str | Path | None = None) -> Path:
    if value or os.environ.get("AUTOSWARM_HOME"):
        return Path(value or os.environ["AUTOSWARM_HOME"]).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "autoswarm"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        while True:
            try:
                os.replace(name, path)
                break
            except PermissionError as error:
                # Windows can briefly deny replacement while a reader or
                # antivirus holds the target. Real access errors still fail.
                if getattr(error, "winerror", None) not in {32, 33}:
                    raise
                time.sleep(0.01)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default


class Records:
    def __init__(self, home: str | Path | None = None):
        self.home = data_home(home)

    def call(self, call_id: str) -> Path:
        if not re.fullmatch(r"call_[a-f0-9]+", call_id):
            raise SwarmError("invalid_call_id", "Invalid AutoSwarm call ID")
        return self.home / "calls" / call_id

    def require(self, call_id: str) -> tuple[Path, dict]:
        directory = self.call(call_id)
        task = read_json(directory / "task.json")
        if task is None:
            raise SwarmError("call_not_found", f"Call not found: {call_id}")
        return directory, task

    def config(self) -> dict:
        return read_json(self.home / "config.json", {})

    def calls(self, parent: dict | None = None):
        for path in sorted((self.home / "calls").glob("call_*")):
            task = read_json(path / "task.json")
            if task and (parent is None or task.get("parent") == parent):
                yield path, task
