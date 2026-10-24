"""Operating-system mechanics, without task deadlines or resource budgets."""
from __future__ import annotations

import ctypes
import errno
import os
import signal
import subprocess
import sys
from pathlib import Path

from .records import SwarmError


def detached_options() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def link_directory(link: Path, target: Path) -> None:
    """Reuse native storage; Windows junctions need no symlink privilege."""
    if os.name != "nt":
        link.symlink_to(target.resolve(), target_is_directory=True)
        return
    # Keep paths in environment data, not shell source. Windows paths cannot
    # contain quotes; disable delayed expansion and avoid CALL re-expansion.
    env = {**os.environ, "AUTOSWARM_LINK_PATH": str(link.resolve()), "AUTOSWARM_TARGET_PATH": str(target.resolve())}
    if any(char in env[key] for key in ("AUTOSWARM_LINK_PATH", "AUTOSWARM_TARGET_PATH") for char in '\r\n"'):
        raise SwarmError("invalid_config_path", "Invalid Windows native-storage path")
    result = subprocess.run([env.get("COMSPEC", "cmd.exe"), "/d", "/v:off", "/c", 'mklink /J "%AUTOSWARM_LINK_PATH%" "%AUTOSWARM_TARGET_PATH%"'], env=env, capture_output=True)
    if result.returncode:
        raise SwarmError("native_config_isolation_failed", "Could not link native session storage: " + result.stderr.decode(errors="replace"))


def unlink_directory(link: Path) -> None:
    if os.name == "nt":
        link.rmdir()  # Remove the junction, never its native target.
    else:
        link.unlink()


def identity(pid: int) -> str | None:
    try:
        if sys.platform.startswith("linux"):
            # comm may contain spaces and parentheses. starttime is field 22.
            stat = Path(f"/proc/{pid}/stat").read_text()
            fields = stat[stat.rfind(")") + 2:].split()
            return None if fields[0] == "Z" else fields[19]
        if os.name == "nt":
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
            kernel.GetProcessTimes.restype = wintypes.BOOL
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetExitCodeProcess.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
                if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
                    return None
                code = wintypes.DWORD()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                    return None
                return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
            finally:
                kernel.CloseHandle(handle)
        value = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="], capture_output=True, text=True).stdout.strip()
        return " ".join(value.split()[:-1]) if value and not value.split()[-1].startswith("Z") else None
    except (OSError, IndexError, ValueError):
        return None


def running(handle: dict | None) -> bool:
    if handle and handle.get("runner_lock"):
        # The shared OS lease is visible across PID namespaces. A missing PID
        # in an inspector's sandbox does not establish that the runner exited.
        try:
            with session_lease(Path(handle["runner_lock"])):
                pass
        except SwarmError as error:
            if error.code != "session_busy":
                raise
            return True
        # A runner can release its lease just before its process exits; keep
        # visible process identity checks for that gap and for legacy handles.
    return bool(handle and handle.get("identity") and identity(handle["pid"]) == handle["identity"])


def stop_child(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def session_lease(path: Path):
    """An OS lock, automatically released on exit; not a resource quota."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError as error:
        stream.close()
        if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise
        raise SwarmError("session_busy", "Another AutoSwarm call owns this native session") from error
