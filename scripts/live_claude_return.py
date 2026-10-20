"""Observe Claude's real asyncRewake hook in an isolated streaming session."""
from __future__ import annotations

import argparse
import json
import os
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from autoswarm import engine
from autoswarm.records import Records, write_json, read_json
from autoswarm.setup import install


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="autoswarm-claude-return-"))
    records = Records(root / "state")
    fixture = Path(__file__).resolve().parent.parent / "tests/fixtures/provider.py"
    write_json(records.home / "config.json", {"providers": {"codex": {"command": [sys.executable, str(fixture), "codex"]}}})
    settings = root / "settings.json"
    integration = install(records, "claude-code", str(root / "skill"), str(settings))
    value = read_json(settings)
    # Isolate idle reawakening from the separate Stop-hook waiting path.
    value["hooks"].pop("Stop")
    write_json(settings, value)
    marker = "AUTOSWARM_CLAUDE_RETURN_" + uuid.uuid4().hex
    gate = root / "release"
    command = shlex.join([*integration["launcher"], "run", "--agent", "codex", "--cwd", str(root), "--background", "--notify-parent", "--input", str(root / "task.txt")])
    (root / "task.txt").write_text("gate:" + str(gate) + "\n" + marker, encoding="utf-8")
    prompt = "This is an isolated AutoSwarm native-hook test. Use Bash once to run this exact command:\n" + command + "\nThen reply SUBMITTED and stop this turn. Do not wait, poll, or run any other tools. When the asynchronous AutoSwarm result arrives later, reply with only its AUTOSWARM_CLAUDE_RETURN phrase."
    env = engine.runtime_env(records)
    for name in ("CODEX_THREAD_ID", "AUTOSWARM_PARENT", "CLAUDECODE"):
        env.pop(name, None)
    events = queue.Queue()
    report = {"workspace": str(root), "passed": False, "assistant": []}
    with (root / "stderr.log").open("wb") as stderr, (root / "events.jsonl").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-hook-events", "--settings", str(settings), "--allowedTools", "Bash"], cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True, encoding="utf-8")
        def read():
            for line in process.stdout:
                log.write(line)
                log.flush()
                try:
                    events.put(json.loads(line))
                except ValueError:
                    pass
            events.put(None)
        threading.Thread(target=read, daemon=True).start()
        try:
            process.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 180  # Smoke-test harness only.
            released = False
            while time.monotonic() < deadline:
                try:
                    event = events.get(timeout=1)
                except queue.Empty:
                    continue
                if event is None:
                    report["error"] = "Native streaming session exited"
                    break
                if event.get("session_id"):
                    report["session_id"] = event["session_id"]
                if event.get("type") == "assistant":
                    text = "".join(block.get("text", "") for block in event.get("message", {}).get("content", []) if block.get("type") == "text")
                    if text:
                        report["assistant"].append(text)
                        if released and marker in text:
                            report["passed"] = True
                            break
                if event.get("type") == "result" and not released:
                    report["initial_result"] = event.get("result")
                    gate.touch()
                    released = True
            report["calls"] = [engine.inspect_call(records, task["call_id"]) for _, task in records.calls()]
            write_json(Path(args.output), report)
            print(json.dumps({key: value for key, value in report.items() if key != "calls"}), flush=True)
        finally:
            for _, task in records.calls():
                engine.cancel(records, task["call_id"])
            process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
