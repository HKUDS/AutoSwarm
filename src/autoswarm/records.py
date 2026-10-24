"""Small, independently written call receipts; native agents own their histories."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from functools import cache
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
                _replace_record(name, path)
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


@cache
def _windows_files():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, wintypes.INT, wintypes.LPVOID, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    return kernel


def _replace_record(source: str, destination: Path) -> None:
    if os.name != "nt":
        os.replace(source, destination)
        return
    import ctypes
    from ctypes import wintypes
    kernel = _windows_files()
    # POSIX rename semantics atomically replace open destinations while their
    # existing readers keep a snapshot. MoveFileEx (os.replace) can deny this.
    class RenameInfo(ctypes.Structure):
        _fields_ = [("flags", wintypes.DWORD), ("root", wintypes.HANDLE), ("length", wintypes.DWORD), ("name", wintypes.WCHAR * 1)]
    encoded = os.path.abspath(destination).encode("utf-16-le")
    buffer = ctypes.create_string_buffer(ctypes.sizeof(RenameInfo) + len(encoded))
    info = RenameInfo.from_buffer(buffer)
    info.flags = 0x1 | 0x2  # REPLACE_IF_EXISTS | POSIX_SEMANTICS
    info.length = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, encoded, len(encoded))
    handle = kernel.CreateFileW(source, 0x10000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.SetFileInformationByHandle(handle, 22, buffer, len(buffer)):  # FileRenameInfoEx
            error = ctypes.get_last_error()
            if error in {50, 87}:  # Filesystem/OS has no extended rename support.
                kernel.CloseHandle(handle)
                handle = None
                os.replace(source, destination)
            else:
                raise ctypes.WinError(error)
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


def _open_record(path: Path):
    if os.name != "nt":
        return path.open(encoding="utf-8")
    import ctypes
    import msvcrt
    kernel = _windows_files()
    # Read access with read/write/delete sharing: a reader keeps its complete
    # snapshot while the atomic writer replaces the pathname. CreateFile also
    # preserves native error codes, unlike CRT open's generic EACCES mapping.
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, "r", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    while True:
        try:
            with _open_record(path) as stream:
                return json.load(stream)
        except FileNotFoundError:
            return default
        except OSError as error:
            if getattr(error, "winerror", None) not in {32, 33}:
                raise
            time.sleep(0.01)


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
