"""Windows-specific SSL environment helpers."""

from __future__ import annotations

import os

_REMOVED_SSLKEYLOGFILE: str | None = None


def sanitize_windows_sslkeylogfile() -> str | None:
    """Eagerly remove SSLKEYLOGFILE on Windows and remember it for one warning."""
    global _REMOVED_SSLKEYLOGFILE
    if os.name != "nt":
        return None
    removed = os.environ.pop("SSLKEYLOGFILE", None)
    if removed and not _REMOVED_SSLKEYLOGFILE:
        _REMOVED_SSLKEYLOGFILE = removed
    return removed


def pop_windows_sslkeylogfile() -> str | None:
    """Remove SSLKEYLOGFILE on Windows to avoid aiohttp/OpenSSL import crashes."""
    global _REMOVED_SSLKEYLOGFILE
    if os.name != "nt":
        return None
    removed = os.environ.pop("SSLKEYLOGFILE", None)
    if removed:
        _REMOVED_SSLKEYLOGFILE = None
        return removed
    remembered = _REMOVED_SSLKEYLOGFILE
    _REMOVED_SSLKEYLOGFILE = None
    return remembered


def format_windows_sslkeylog_warning(command_label: str, keylog_path: str) -> str:
    """Render a consistent warning when SSLKEYLOGFILE must be ignored on Windows."""
    return (
        f"Warning: ignoring SSLKEYLOGFILE for `{command_label}` on Windows "
        f"({keylog_path}) because it can crash aiohttp/OpenSSL."
    )
