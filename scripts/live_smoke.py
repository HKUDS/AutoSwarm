"""Optional real-provider checks. Uses existing native auth; spends model calls."""
from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from pathlib import Path

from autoswarm.engine import resume, start_call, wait
from autoswarm.records import Records, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", nargs="+", default=["codex", "claude-code", "opencode"])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="autoswarm-smoke-"))
    records = Records(root / "state")
    calls = []
    report = {"workspace": str(root), "providers": {}}
    for provider in args.providers:
        token = "AUTOSWARM_" + uuid.uuid4().hex
        info = start_call(records, provider, f"Remember the exact phrase {token}. Reply with only that phrase. Do not call tools, read files, or modify files.", root, return_mode="wait")
        calls.append((provider, token, info["call_id"]))
    for provider, token, call_id in calls:
        result = wait(records, [call_id])[0]
        entry = {"initial": result, "passed": False}
        report["providers"][provider] = entry
        if result["outcome"] == "completed" and token in result["content"]:
            continued = resume(records, call_id, "What exact phrase did I ask you to remember? Reply with only that phrase. Do not call tools.", return_mode="wait")
            value = wait(records, [continued["call_id"]])[0]
            entry["resumed"] = value
            entry["passed"] = value["outcome"] == "completed" and token in value["content"] and value["native_session_id"] == result["native_session_id"]
        write_json(Path(args.output), report)
        print(json.dumps({"provider": provider, "passed": entry["passed"], "outcome": result["outcome"], "error": result.get("error")}), flush=True)
    raise SystemExit(0 if all(entry["passed"] for entry in report["providers"].values()) else 1)


if __name__ == "__main__":
    main()
