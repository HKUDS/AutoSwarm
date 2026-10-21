"""Optional real-provider checks. Uses existing native auth; spends model calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from autoswarm.doctor import probe
from autoswarm.records import Records, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", nargs="+", default=["codex", "claude-code", "opencode"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--home", help="Existing AutoSwarm config/state; defaults to the normal home")
    parser.add_argument("--cwd", default=os.getcwd(), help="Use the same native project/config as the actual task")
    parser.add_argument("--model", help="Optional native model selection for these calls")
    parser.add_argument("--effort", help="Optional native reasoning effort/variant for these calls")
    args = parser.parse_args()
    records = Records(args.home)
    report = {"workspace": str(Path(args.cwd).resolve()), "providers": {}}
    for provider in args.providers:
        entry = probe(records, provider, args.cwd, model=args.model, effort=args.effort)
        entry["passed"] = entry["verified"]
        report["providers"][provider] = entry
        write_json(Path(args.output), report)
        print(json.dumps({"provider": provider, "passed": entry["passed"], "error": entry.get("error") or entry.get("initial", {}).get("error")}), flush=True)
    raise SystemExit(0 if all(entry["passed"] for entry in report["providers"].values()) else 1)


if __name__ == "__main__":
    main()
