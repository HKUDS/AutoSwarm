"""Smoke test for the remote peer transport without real network calls.

Creates two isolated AUTOSWARM_HOME directories on the same machine, starts
two HTTP listeners on different ports, and verifies that a message sent from
one swarm reaches the other.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from autoswarm.records import Records
from autoswarm import messages
from autoswarm.remote import get_transport, _transport_for


def make_home():
    tmp = Path(tempfile.mkdtemp(prefix="autoswarm-remote-test-"))
    (tmp / "config.json").write_text(json.dumps({"remote_peers": {}}), encoding="utf-8")
    return tmp


# Module-level singleton caching causes transport state to leak between tests,
# but here we just need one run. Import and reset the cache explicitly.
from autoswarm import remote as _remote_module


def test_roundtrip():
    _remote_module._TRANSPORT_CACHE.clear()
    home_a = make_home()
    home_b = make_home()

    os.environ["AUTOSWARM_REMOTE_PORT"] = "18787"
    os.environ["AUTOSWARM_REMOTE_HOST"] = "127.0.0.1"
    records_a = Records(home_a)
    transport_a = _transport_for(records_a)
    assert transport_a is not None

    os.environ["AUTOSWARM_REMOTE_PORT"] = "18788"
    records_b = Records(home_b)
    transport_b = _transport_for(records_b)
    assert transport_b is not None

    # Use actual bound ports for peers (important on macOS where requested ports
    # may be unavailable due to TIME_WAIT).
    transport_a.add_peer("b", f"http://127.0.0.1:{transport_b.port}")
    transport_b.add_peer("a", f"http://127.0.0.1:{transport_a.port}")

    # When sending, messages.py reads AUTOSWARM_REMOTE_PORT to locate the cache.
    os.environ["AUTOSWARM_REMOTE_PORT"] = str(transport_a.port)

    # Shared ids used on both sides.
    agent_id = "call_" + "b" * 32
    main_a = "main_" + "a" * 32

    # A's local view of B's agent: a stub that says where to deliver messages.
    # It is owned by A's main controller so A is allowed to send to it.
    (records_a.home / "agents" / agent_id).mkdir(parents=True, exist_ok=True)
    (records_a.home / "agents" / agent_id / "agent.json").write_text(
        json.dumps({"agent_id": agent_id, "call_id": agent_id, "remote_peer": "b", "owner_id": main_a}),
        encoding="utf-8",
    )

    # Create a fake local agent on B that A will address, plus its matching call record.
    call_dir = records_b.call(agent_id)
    call_dir.mkdir(parents=True, exist_ok=True)
    (call_dir / "task.json").write_text(
        json.dumps({"call_id": agent_id, "provider": "dummy", "workspace": str(home_b)}),
        encoding="utf-8",
    )
    (records_b.home / "agents" / agent_id).mkdir(parents=True, exist_ok=True)
    (records_b.home / "agents" / agent_id / "agent.json").write_text(
        json.dumps({"agent_id": agent_id, "call_id": agent_id, "remote_peer": "a"}),
        encoding="utf-8",
    )

    # Pretend A has the controller main agent.
    (records_a.home / "agents" / main_a).mkdir(parents=True, exist_ok=True)
    (records_a.home / "agents" / main_a / "agent.json").write_text(
        json.dumps({"agent_id": main_a, "owner_id": None, "call_id": None}),
        encoding="utf-8",
    )

    result = messages.send(records_a, agent_id, "Hello from A", sender=main_a)
    assert result["delivery"]["state"] == "submitted", result

    # Give the HTTP server a moment.
    time.sleep(0.3)

    received = messages.receive(records_b, agent_id)
    assert len(received) == 1, received
    assert received[0]["content"] == "Hello from A"

    transport_a.stop()
    transport_b.stop()
    print("OK: remote message roundtrip")


if __name__ == "__main__":
    test_roundtrip()
