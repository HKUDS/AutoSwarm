"""Observe an installed plugin returning a child result to real OpenCode."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from autoswarm import engine
from autoswarm.hosts import _http
from autoswarm.records import Records, write_json
from autoswarm.setup import install


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--real-child", action="store_true", help="Use another real OpenCode CLI instead of the controlled child")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="autoswarm-opencode-return-"))
    records = Records(root / "state")
    fixture = Path(__file__).resolve().parent.parent / "tests/fixtures/provider.py"
    write_json(records.home / "config.json", {"providers": {"codex": {"command": [sys.executable, str(fixture), "codex"]}}})
    integration = install(records, "opencode", str(root / "skill"), plugin_dir=str(root / "plugins"))
    native_modules = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode/node_modules"
    if native_modules.exists():
        (root / "node_modules").symlink_to(native_modules, target_is_directory=True)
    config = root / "opencode.json"
    write_json(config, {"plugin": [Path(integration["plugin"]).as_uri()]})
    marker = "AUTOSWARM_OPENCODE_RETURN_" + uuid.uuid4().hex
    gate = root / "release"
    task = root / "task.txt"
    task.write_text("Reply with only " + marker + ". Do not use tools." if args.real_child else "gate:" + str(gate) + "\n" + marker, encoding="utf-8")
    argv = ["run", "--agent", "opencode" if args.real_child else "codex", "--cwd", str(root), "--background", "--input", str(task)]
    prompt = "This is an isolated AutoSwarm plugin test. Use the autoswarm tool once with arguments " + json.dumps(argv) + ". Then reply SUBMITTED and stop this turn. Do not wait, poll, or use any other tools. When the asynchronous AutoSwarm result arrives, reply with only its AUTOSWARM_OPENCODE_RETURN phrase."
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    parent = {"host": "opencode", "endpoint": f"http://127.0.0.1:{port}", "workspace": str(root)}
    report = {"workspace": str(root), "passed": False}
    env = engine.runtime_env(records)
    env["OPENCODE_CONFIG"] = str(config)
    env.pop("AUTOSWARM_PARENT", None)
    env.pop("CODEX_THREAD_ID", None)
    with (root / "server.log").open("wb") as log:
        process = subprocess.Popen(["opencode", "serve", "--hostname", "127.0.0.1", "--port", str(port), "--print-logs"], cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            while "server listening on" not in (root / "server.log").read_text(encoding="utf-8", errors="replace"):
                if process.poll() is not None:
                    raise RuntimeError("Native OpenCode exited before readiness")
                time.sleep(0.1)
            previous_timeout = socket.getdefaulttimeout()
            while True:
                try:
                    socket.setdefaulttimeout(1)  # Readiness probe only, not a task limit.
                    _http(parent, "GET", "/global/health")
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError("Native OpenCode exited; inspect " + str(root / "server.log"))
                    time.sleep(0.1)
                finally:
                    socket.setdefaulttimeout(previous_timeout)
            tools = _http(parent, "GET", "/experimental/tool/ids")
            if "autoswarm" not in tools:
                raise RuntimeError("Native OpenCode did not load the installed autoswarm tool")
            print("Native AutoSwarm tool loaded", flush=True)
            session = _http(parent, "POST", "/session", {"title": "AutoSwarm isolated return test"})
            session_id = session["id"]
            report["session_id"] = session_id
            print("Native test session created: " + session_id, flush=True)
            initial = _http(parent, "POST", f"/session/{session_id}/message", {"parts": [{"type": "text", "text": prompt}]})
            report["initial"] = initial
            write_json(Path(args.output), report)
            gate.touch()
            deadline = time.monotonic() + 180  # Smoke-test harness only.
            while time.monotonic() < deadline:
                messages = _http(parent, "GET", f"/session/{session_id}/message")
                assistant = ["".join(part.get("text", "") for part in message.get("parts", []) if part.get("type") == "text") for message in messages if message.get("info", {}).get("role") == "assistant"]
                calls = [engine.inspect_call(records, task["call_id"]) for _, task in records.calls()]
                if any(marker in text for text in assistant) and any((call["delivery"] or {}).get("continued") for call in calls):
                    report.update({"passed": True, "assistant": assistant, "calls": calls})
                    break
                time.sleep(0.1)
            if not report["passed"]:
                report.update({"assistant": assistant, "calls": calls})
            write_json(Path(args.output), report)
            print(json.dumps({key: value for key, value in report.items() if key not in {"initial", "calls"}}), flush=True)
        finally:
            for _, task in records.calls():
                engine.cancel(records, task["call_id"])
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
