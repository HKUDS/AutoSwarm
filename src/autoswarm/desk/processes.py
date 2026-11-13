"""Processes started from one folder, for Desk's updater (desktop/electron/updates.cjs).

Desk's installer replaces its folder and stops everything running from it: agents' runners and
AutoSwarm commands use Desk's bundled Python, so an update waits until none of them run.

    python -m autoswarm.desk.processes FOLDER [--children-of PID] [--except-name NAME]

prints a JSON list of {"pid", "path"}. --children-of leaves out the processes PID started
(Desk's own watcher and commands); a process counts as its child only if it started later,
as process IDs are reused. Windows only; elsewhere the list is empty.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys

from ..platform import identity

TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _snapshot():
    """(pid, parent pid, executable name) of every process."""
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32FirstW.restype = kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            yield entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)


def _image(pid: int) -> str | None:
    """Full executable path, for processes of this user (others cannot be opened)."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        return buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else None
    finally:
        kernel.CloseHandle(handle)


def running_from(folder: str, children_of: int | None = None, except_name: str | None = None) -> list[dict]:
    if os.name != "nt":
        return []
    prefix = os.path.normcase(os.path.abspath(folder)).rstrip("\\/") + os.sep
    parent_started = identity(children_of) if children_of else None
    found = []
    for pid, parent, name in _snapshot():
        if pid in (0, os.getpid()) or (except_name and name.lower() == except_name.lower()):
            continue
        path = _image(pid)
        if not path or not os.path.normcase(path).startswith(prefix):
            continue
        if parent_started and parent == children_of:
            started = identity(pid)
            if started and int(started) >= int(parent_started):
                continue
        found.append({"pid": pid, "path": path})
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Processes started from one folder (JSON)")
    parser.add_argument("folder")
    parser.add_argument("--children-of", type=int)
    parser.add_argument("--except-name")
    args = parser.parse_args(argv)
    print(json.dumps(running_from(args.folder, args.children_of, args.except_name)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
