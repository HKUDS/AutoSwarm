"""Terminate an owned Windows process tree through native process handles."""
from __future__ import annotations

import ctypes
from functools import cache


@cache
def _api():
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "Process32FirstW": ([wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
        "Process32NextW": ([wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "GetProcessTimes": ([wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)], wintypes.BOOL),
        "GetSystemTimePreciseAsFileTime": ([ctypes.POINTER(wintypes.FILETIME)], None),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel, ProcessEntry


def _ticks(value):
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


def _created(kernel, handle):
    from ctypes import wintypes
    created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    return _ticks(created)


def _snapshot(kernel, entry_type):
    from ctypes import wintypes
    cutoff = wintypes.FILETIME()
    kernel.GetSystemTimePreciseAsFileTime(ctypes.byref(cutoff))
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = entry_type()
        entry.dwSize = ctypes.sizeof(entry)
        rows = []
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            rows.append((entry.th32ProcessID, entry.th32ParentProcessID))
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
        return _ticks(cutoff), rows
    finally:
        kernel.CloseHandle(snapshot)


EXIT_GRACE_MS = 5000


def _denied(kernel, pid, parent_born, cutoff):
    """Classify a process listed under a tree member that denied terminate rights.

    A snapshot keeps the parent PID a process was started with, and Windows reuses PIDs, so an
    unrelated protected or system process can name a member's PID as its parent; terminate rights
    are denied for it even to administrators. Query rights still show its start time. A member
    that has begun exiting can also deny them briefly; wait a little for that exit instead.
    Returns "unrelated", "deferred" (started after the snapshot; the rescan decides), "exited",
    or "member" for a running member that cannot be stopped.
    """
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)  # query, synchronize
    if not handle:
        return "unrelated"  # Nothing shows that it belongs to the tree.
    try:
        born = _created(kernel, handle)
        if born < parent_born:
            return "unrelated"
        if born > cutoff:
            return "deferred"
        return "exited" if kernel.WaitForSingleObject(handle, EXIT_GRACE_MS) == 0 else "member"
    finally:
        kernel.CloseHandle(handle)


def stop_tree(process):
    kernel, entry_type = _api()
    # Popen keeps this handle open even after exit, so the root PID cannot
    # be reused while we find descendants whose parent has already exited.
    root_handle = int(process._handle)
    owned = {process.pid: (root_handle, _created(kernel, root_handle))}
    first = True
    try:
        while True:
            cutoff, rows = _snapshot(kernel, entry_type)
            pending = dict(rows)
            added = deferred = False
            while True:
                progress = False
                for pid, parent in list(pending.items()):
                    if pid in owned or parent not in owned:
                        continue
                    del pending[pid]
                    handle = kernel.OpenProcess(0x1 | 0x1000 | 0x100000, False, pid)  # terminate, query, synchronize
                    if not handle:
                        error = ctypes.get_last_error()
                        if error == 87:  # Process exited between snapshot and open.
                            continue
                        if error == 5:
                            state = _denied(kernel, pid, owned[parent][1], cutoff)
                            if state == "deferred":
                                deferred = True
                            if state != "member":
                                continue
                        raise ctypes.WinError(error)
                    try:
                        born = _created(kernel, handle)
                        # Old orphaned PIDs and PIDs reused after the snapshot
                        # do not establish ownership of a process.
                        if born > cutoff:
                            deferred = True
                        elif born >= owned[parent][1]:
                            owned[pid] = (handle, born)
                            handle = None  # Retain until every tree member exits.
                            progress = added = True
                    finally:
                        if handle is not None:
                            kernel.CloseHandle(handle)
                if not progress:
                    break

            # Stop parents first to prevent more work being spawned. Signal
            # all members before waiting, then rescan for children born during
            # the previous enumeration. No task or execution deadline is added.
            for handle, _ in owned.values():
                state = kernel.WaitForSingleObject(handle, 0)
                if state == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if state == 258 and not kernel.TerminateProcess(handle, 1):
                    error = ctypes.get_last_error()
                    # Every retained handle has PROCESS_TERMINATE rights.
                    # Windows also returns ACCESS_DENIED when a process has
                    # begun exiting but its handle is not signaled yet.
                    # Wait below for that concurrent exit to finish.
                    if error != 5:
                        raise ctypes.WinError(error)
            for handle, _ in owned.values():
                if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) != 0:
                    raise ctypes.WinError(ctypes.get_last_error())
            if not first and not added and not deferred:
                return
            first = False
    finally:
        for pid, (handle, _) in owned.items():
            if pid != process.pid:
                kernel.CloseHandle(handle)
