"""Explicit real invocation checks using the same calls and native configuration."""
from __future__ import annotations

import uuid

from . import engine
from .records import Records, SwarmError


def probe(records: Records, provider: str, workspace: str) -> dict:
    token = "AUTOSWARM_CHECK_" + uuid.uuid4().hex
    report = {"provider": provider, "verified": False, "workspace": workspace}
    try:
        initial = engine.start_call(records, provider, f"Remember the exact phrase {token}. Reply with only that phrase. Do not call tools or modify files.", workspace, parent=None, return_mode="wait")
        report["initial_call_id"] = initial["call_id"]
        result = engine.wait(records, [initial["call_id"]])[0]
        report["initial"] = result
        if result.get("outcome") != "completed" or token not in result.get("content", ""):
            return report
        continued = engine.resume(records, initial["call_id"], "What exact phrase did I ask you to remember? Reply with only that phrase. Do not call tools.", parent=None, inherit_parent=False, return_mode="wait")
        report["resume_call_id"] = continued["call_id"]
        resumed = engine.wait(records, [continued["call_id"]])[0]
        report["resumed"] = resumed
        report["verified"] = resumed.get("outcome") == "completed" and token in resumed.get("content", "") and bool(result.get("native_session_id")) and resumed.get("native_session_id") == result["native_session_id"]
    except (SwarmError, OSError, ValueError) as error:
        report["error"] = {"code": getattr(error, "code", "probe_failed"), "message": str(error)}
    return report
