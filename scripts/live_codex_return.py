"""Exercise real Codex host continuation without touching an existing user chat."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from autoswarm.hosts import deliver
from autoswarm.records import Records, write_json
from autoswarm.wire import RPC


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="autoswarm-host-smoke-"))
    records = Records(root / "state")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"ws://127.0.0.1:{port}"
    report = {"workspace": str(root), "cases": []}
    with (root / "host.log").open("wb") as log:
        process = subprocess.Popen(["codex", "app-server", "--listen", endpoint], stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            while True:
                try:
                    controller = RPC(endpoint)
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError("Native app-server exited; inspect " + str(root / "host.log"))
                    time.sleep(0.1)
            with controller:
                thread = controller.request("thread/start", {"cwd": str(root), "developerInstructions": "This is an isolated AutoSwarm continuation test. Do not use tools. When a child result arrives, answer with only its AUTOSWARM_RETURN phrase."})["thread"]["id"]
                initial = controller.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "Reply READY. Do not use tools."}]})["turn"]["id"]
                while True:
                    event = controller.receive()
                    if event.get("method") == "turn/completed" and event.get("params", {}).get("turn", {}).get("id") == initial:
                        if event["params"]["turn"]["status"] != "completed":
                            raise RuntimeError("Initial native parent turn failed")
                        break
                for scenario in ("idle", "busy"):
                    if scenario == "busy":
                        controller.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "A child result will arrive shortly. Reply WAITING unless it has arrived; do not use tools."}]})
                    call_id = "call_" + uuid.uuid4().hex
                    marker = "AUTOSWARM_RETURN_" + uuid.uuid4().hex
                    directory = records.call(call_id)
                    write_json(directory / "task.json", {"call_id": call_id, "provider": "codex", "parent": {"host": "codex", "session_id": thread, "endpoint": endpoint}})
                    write_json(directory / "result.json", {"call_id": call_id, "provider": "codex", "outcome": "completed", "content": marker, "error": None})
                    receipt = deliver(records, call_id)
                    report["cases"].append({"scenario": scenario, "receipt": receipt, "passed": False})
                    write_json(Path(args.output), report)
                    if receipt.get("state") != "delivered":
                        raise RuntimeError(json.dumps(receipt))
                    assistant = []
                    while True:
                        event = controller.receive()
                        params = event.get("params", {})
                        if event.get("method") == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
                            assistant.append(params["item"].get("text", ""))
                        if event.get("method") == "turn/completed" and params.get("turn", {}).get("id") == receipt["turn_id"]:
                            case = report["cases"][-1]
                            case["assistant"] = assistant
                            case["passed"] = marker in "\n".join(assistant) and params["turn"]["status"] == "completed"
                            write_json(Path(args.output), report)
                            print(json.dumps(case), flush=True)
                            break
        finally:
            process.terminate()
            process.wait()
    raise SystemExit(0 if all(case["passed"] for case in report["cases"]) else 1)


if __name__ == "__main__":
    main()
