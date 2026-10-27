from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import approvals, engine, platform
from autoswarm.hooks import handle_hook
from autoswarm.providers import BINARIES
from autoswarm.records import Records, SwarmError, read_json, write_json

if __package__:
    from .autoswarm_test_support import cleanup_temporary
else:
    from autoswarm_test_support import cleanup_temporary

FIXTURE = Path(__file__).parent / "fixtures/provider.py"


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.records = Records(self.root / "state")
        write_json(self.records.home / "config.json", {"providers": {name: {"command": [sys.executable, str(FIXTURE), name], **({"python": [sys.executable, str(FIXTURE), name]} if name == "nanobot" else {})} for name in BINARIES}})
        self.ids = []

    def tearDown(self):
        for call_id in self.ids:
            engine.cancel(self.records, call_id)
        deadline = time.monotonic() + 10  # Test harness only.
        # A result can be published before the runner closes its log/leases.
        # Windows requires those handles to close before removing the fixture.
        while any(platform.running(read_json(self.records.call(value) / "handle.json")) for value in self.ids) and time.monotonic() < deadline:
            time.sleep(0.01)
        cleanup_temporary(self.temporary, deadline)

    def start(self, provider="codex", prompt=None, **kwargs):
        marker = self.root / ("executed-" + str(len(self.ids)))
        call = engine.start_call(self.records, provider, prompt or "approval:" + str(marker), self.root, **kwargs)
        self.ids.append(call["call_id"])
        return call["call_id"], marker

    def test_all_native_transports_pause_then_continue_same_invocation(self):
        for provider in BINARIES:
            with self.subTest(provider=provider):
                call_id, marker = self.start(provider)
                request = engine.wait(self.records, [call_id])[0]
                self.assertEqual(request["kind"], "approval_required")
                self.assertTrue(request["delegate_to_parent"])
                self.assertFalse(marker.exists())
                info = engine.inspect_call(self.records, call_id)
                self.assertEqual(info["state"], "running")
                self.assertIsNone(info["result"])
                self.assertEqual(len(info["approvals"]), 1)
                self.assertFalse((self.records.call(call_id) / "received.json").exists())
                approvals.decide(self.records, call_id, request["request_id"], "allow")
                result = engine.wait(self.records, [call_id])[0]
                self.assertEqual(result["outcome"], "completed", result)
                self.assertEqual(result["native_session_id"], request["native_session_id"])
                self.assertEqual(result["call_id"], call_id)
                self.assertTrue(marker.exists())
                self.assertIn("approved", result["content"])
                self.assertEqual(approvals.pending(self.records, call_id), [])

    def test_all_native_transports_relay_denial_without_executing(self):
        for provider in BINARIES:
            with self.subTest(provider=provider):
                call_id, marker = self.start(provider)
                request = engine.wait(self.records, [call_id])[0]
                approvals.decide(self.records, call_id, request["request_id"], "deny", message="Use a different approach")
                result = engine.wait(self.records, [call_id])[0]
                self.assertEqual(result["outcome"], "completed")
                self.assertIn("denied", result["content"])
                self.assertFalse(marker.exists())

    def test_approval_stays_answerable_without_runner_pid_visibility(self):
        call_id, marker = self.start()
        request = engine.wait(self.records, [call_id])[0]
        with patch("autoswarm.platform.identity", return_value=None):
            self.assertEqual(approvals.pending(self.records, call_id)[0]["request_id"], request["request_id"])
            approvals.decide(self.records, call_id, request["request_id"], "allow")
            result = engine.wait(self.records, [call_id])[0]
        self.assertEqual(result["outcome"], "completed")
        self.assertTrue(marker.exists())

    def test_disabled_delegation_requires_explicit_user_actor(self):
        approvals.configure(self.records, False)
        call_id, marker = self.start("claude-code")
        request = engine.wait(self.records, [call_id])[0]
        self.assertFalse(request["delegate_to_parent"])
        with self.assertRaises(SwarmError) as caught:
            approvals.decide(self.records, call_id, request["request_id"], "allow")
        self.assertEqual(caught.exception.code, "user_approval_required")
        self.assertFalse(marker.exists())
        approvals.decide(self.records, call_id, request["request_id"], "allow", actor="user")
        self.assertEqual(engine.wait(self.records, [call_id])[0]["outcome"], "completed")
        self.assertTrue(marker.exists())

    def test_muse_can_relay_rejection_when_native_offers_only_abort(self):
        call_id, marker = self.start("muse", provider_args=["abort-only"])
        request = engine.wait(self.records, [call_id])[0]
        approvals.decide(self.records, call_id, request["request_id"], "deny")
        result = engine.wait(self.records, [call_id])[0]
        self.assertEqual(result["outcome"], "completed")
        self.assertIn("denied", result["content"])
        self.assertFalse(marker.exists())

    def test_config_toggle_applies_to_pending_requests_and_preserves_provider_config(self):
        call_id, _ = self.start()
        request = engine.wait(self.records, [call_id])[0]
        config = self.records.config()
        approvals.configure(self.records, False)
        self.assertEqual(self.records.config()["providers"], config["providers"])
        self.assertFalse(approvals.pending(self.records, call_id)[0]["delegate_to_parent"])
        with self.assertRaises(SwarmError):
            approvals.decide(self.records, call_id, request["request_id"], "allow")
        approvals.configure(self.records, True)
        approvals.decide(self.records, call_id, request["request_id"], "deny")
        engine.wait(self.records, [call_id])

    def test_per_call_override_and_resume_inheritance(self):
        approvals.configure(self.records, False)
        call_id, _ = self.start(delegate_approvals=True)
        request = engine.wait(self.records, [call_id])[0]
        self.assertTrue(request["delegate_to_parent"])
        approvals.decide(self.records, call_id, request["request_id"], "allow")
        engine.wait(self.records, [call_id])
        continued = engine.resume(self.records, call_id, "hello")
        self.ids.append(continued["call_id"])
        self.assertTrue(continued["delegate_approvals"])
        self.assertEqual(engine.wait(self.records, [continued["call_id"]])[0]["native_session_id"], "fixture-session")

    def test_wait_all_returns_approval_without_waiting_for_other_children(self):
        held, _ = self.start(prompt="hold:" + str(self.root / "held"))
        call_id, _ = self.start("claude-code")
        events = engine.wait(self.records, [held, call_id])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "approval_required")
        self.assertEqual(engine.inspect_call(self.records, held)["state"], "running")

    def test_cancellation_withdraws_permission_and_rejects_late_approval(self):
        call_id, marker = self.start("opencode")
        request = engine.wait(self.records, [call_id])[0]
        engine.cancel(self.records, call_id)
        with self.assertRaises(SwarmError):
            approvals.decide(self.records, call_id, request["request_id"], "allow")
        self.assertEqual(engine.wait(self.records, [call_id])[0]["outcome"], "cancelled")
        path = approvals.location(self.records, call_id, request["request_id"])
        self.assertEqual(read_json(path / "resolution.json")["state"], "cancelled")
        self.assertFalse(marker.exists())

    def test_request_cannot_be_answered_through_another_call(self):
        first, _ = self.start()
        second, _ = self.start("claude-code")
        request = engine.wait(self.records, [first])[0]
        with self.assertRaises(SwarmError) as caught:
            approvals.decide(self.records, second, request["request_id"], "allow")
        self.assertEqual(caught.exception.code, "approval_not_found")
        with self.assertRaises(SwarmError):
            approvals.location(self.records, first, "../../elsewhere")

    def test_decisions_are_idempotent_but_cannot_be_overwritten(self):
        call_id, _ = self.start()
        request = engine.wait(self.records, [call_id])[0]
        decision = approvals.decide(self.records, call_id, request["request_id"], "allow")
        self.assertEqual(approvals.decide(self.records, call_id, request["request_id"], "allow"), decision)
        with self.assertRaises(SwarmError):
            approvals.decide(self.records, call_id, request["request_id"], "deny")
        engine.wait(self.records, [call_id])

    def test_claude_stop_surfaces_approval_then_allows_turn_to_end_for_user(self):
        parent = {"host": "claude-code", "session_id": "parent", "mode": "hook"}
        call_id, _ = self.start(parent=parent, delegate_approvals=False)
        engine.wait(self.records, [call_id], mark_received=False)
        first = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"session_id":"parent"}')), contextlib.redirect_stdout(first):
            handle_hook(self.records, "stop")
        self.assertEqual(json.loads(first.getvalue())["decision"], "block")
        self.assertIn("DISABLED", first.getvalue())
        second = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"session_id":"parent"}')), contextlib.redirect_stdout(second):
            handle_hook(self.records, "stop")
        self.assertEqual(second.getvalue(), "")
        self.assertEqual(engine.inspect_call(self.records, call_id)["state"], "running")

    def test_claude_async_approval_is_separate_from_final_delivery(self):
        parent = {"host": "claude-code", "session_id": "parent", "mode": "hook"}
        call_id, _ = self.start(parent=parent)
        request = engine.wait(self.records, [call_id], mark_received=False)[0]
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"session_id":"parent"}')), contextlib.redirect_stderr(output):
            self.assertEqual(handle_hook(self.records, "post-tool"), 2)
        self.assertIn("approval required", output.getvalue())
        path = approvals.location(self.records, call_id, request["request_id"])
        self.assertEqual(read_json(path / "delivery.json")["state"], "submitted")
        self.assertFalse((self.records.call(call_id) / "received.json").exists())
        self.assertEqual(approvals.pending(self.records, call_id, unseen=True), [])
        approvals.decide(self.records, call_id, request["request_id"], "allow")
        self.assertEqual(engine.wait(self.records, [call_id], unseen=True)[0]["outcome"], "completed")

    def test_cli_config_and_approval_commands(self):
        env = engine.runtime_env(self.records)
        def invoke(*args):
            process = subprocess.run([*engine.runtime_argv(), *args], env=env, text=True, capture_output=True, check=True)
            return json.loads(process.stdout)["data"]
        self.assertTrue(invoke("config")["delegate_approvals"])
        self.assertFalse(invoke("config", "--delegate-approvals", "off")["delegate_approvals"])
        call_id, _ = self.start(delegate_approvals=True)
        request = engine.wait(self.records, [call_id])[0]
        self.assertEqual(invoke("approvals", call_id)[0]["request_id"], request["request_id"])
        self.assertEqual(invoke("approve", call_id, request["request_id"])["actor"], "parent")
        engine.wait(self.records, [call_id])
