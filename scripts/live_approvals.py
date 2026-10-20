"""Exercise real native permission round trips in isolated local workspaces."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from autoswarm import approvals, engine
from autoswarm.records import Records, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", nargs="+", default=["codex", "claude-code", "opencode"])
    parser.add_argument("--decision", choices=["allow", "deny"], default="allow")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="autoswarm-native-approvals-"))
    records = Records(root / "state")
    # Force a native ASK for a harmless, explicitly requested test operation.
    # No native allow/deny rules are changed in the user's own configuration.
    config = {"providers": {
        "codex": {"args": ["--sandbox", "read-only", "--ask-for-approval", "on-request", "-c", 'approvals_reviewer="user"']},
        "claude-code": {"args": ["--permission-mode", "default", "--settings", json.dumps({"permissions": {"ask": ["Bash"]}})]},
    }}
    write_json(records.home / "config.json", config)
    report = {"workspace": str(root), "decision": args.decision, "cases": {}}
    for provider in args.providers:
        workspace = root / provider
        workspace.mkdir()
        if provider == "opencode":
            write_json(workspace / "opencode.json", {"permission": {"bash": "ask"}})
        marker = workspace / "approval-proof.txt"
        command = "printf AUTOSWARM_NATIVE_APPROVAL > " + str(marker)
        prompt = ("This is an isolated native permission integration test. Run exactly this shell command once using your shell/Bash tool: " + command +
                  ". Request the native permission required for it; for a read-only sandbox, request escalation through the command tool. "
                  "Do not use editing tools or another command to create the file. If permission is denied, stop trying and report DENIED. "
                  "After the command completes, report DONE. Do not run cleanup or additional checks.")
        info = engine.start_call(records, provider, prompt, workspace, return_mode="wait")
        report["cases"][provider] = {"call_id": info["call_id"], "marker": str(marker), "passed": False}
    write_json(Path(args.output), report)
    print(json.dumps({"workspace": str(root), "calls": {name: case["call_id"] for name, case in report["cases"].items()}}, ensure_ascii=False), flush=True)
    for provider, case in report["cases"].items():
        requests = []
        while True:
            values = engine.wait(records, [case["call_id"]])
            final = next((value for value in values if value.get("kind") != "approval_required"), None)
            if final:
                case["result"] = final
                break
            for request in values:
                assert not Path(case["marker"]).exists(), "Operation ran before approval"
                requests.append(request)
                print(json.dumps({"provider": provider, "request": request["request_id"], "native": request["native_request"]}, ensure_ascii=False), flush=True)
                approvals.decide(records, case["call_id"], request["request_id"], args.decision, message="Explicitly requested isolated integration test")
        case["requests"] = requests
        case["marker_exists"] = Path(case["marker"]).exists()
        expected_outcomes = {"completed"} if args.decision == "allow" else {"completed", "cancelled"}
        case["passed"] = bool(requests) and final["outcome"] in expected_outcomes and final["native_session_id"] == requests[0]["native_session_id"] and case["marker_exists"] == (args.decision == "allow")
        write_json(Path(args.output), report)
        print(json.dumps({"provider": provider, "passed": case["passed"], "outcome": final["outcome"], "error": final.get("error")}, ensure_ascii=False), flush=True)
    raise SystemExit(0 if all(case["passed"] for case in report["cases"].values()) else 1)


if __name__ == "__main__":
    main()
