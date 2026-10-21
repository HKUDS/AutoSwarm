from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from autoswarm import approvals, engine, messages, platform
from autoswarm.providers import BINARIES
from autoswarm.records import Records, SwarmError, read_json, write_json

FIXTURE = Path(__file__).parent / "fixtures/conversation.py"


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.records = Records(self.root / "state")
        write_json(self.records.home / "config.json", {"providers": {name: {"command": [sys.executable, str(FIXTURE), name], **({"python": [sys.executable, str(FIXTURE), name]} if name == "nanobot" else {})} for name in BINARIES}})
        self.controller = "main_" + uuid.uuid4().hex
        write_json(messages.agent_path(self.records, self.controller) / "agent.json", {"agent_id": self.controller, "call_id": None, "owner_id": None})

    def eventually(self, predicate):
        # Only the test harness has a deadline; the runtime has no task limit.
        end = time.monotonic() + 15
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(.02)
        self.fail("Condition not reached; logs: " + repr({str(p): p.read_text(errors="replace") for p in self.records.home.glob("calls/*/runner.log")}))

    def tearDown(self):
        for path, task in self.records.calls():
            engine.cancel(self.records, task["call_id"])
        self.eventually(lambda: all(not platform.running(read_json(path / "handle.json")) for path, _ in self.records.calls()))
        self.tmp.cleanup()

    def start(self, provider="codex", name=None, peers=None, approval=False, **selection):
        key = uuid.uuid4().hex
        gate, proof = self.root / (key + ".gate"), self.root / (key + ".proof")
        call = engine.start_call(self.records, provider, json.dumps({"gate": str(gate), "proof": str(proof), "approval": approval}), self.root,
                                 controller=self.controller, name=name, peers=peers, rules="Reply to questions and continue assigned work.", return_mode="wait", **selection)
        self.eventually(lambda: (self.root / ("events-" + call["agent_id"])).exists())
        return call, gate, proof

    def result(self, call):
        return self.eventually(lambda: read_json(self.records.call(call["call_id"]) / "result.json"))

    def reply(self, message):
        return [value for value in messages.wait(self.records, message["message_id"], sender=self.controller, block=False)
                if value.get("reply_to") == message["message_id"]]

    def test_urgent_reply_before_completion_then_original_work_continues_all_providers(self):
        for provider in BINARIES:
            with self.subTest(provider=provider):
                call, gate, proof = self.start(provider)
                sent = messages.send(self.records, call["agent_id"], "urgent question 中文", sender=self.controller, interrupt=True)
                replies = self.eventually(lambda: self.reply(sent))
                self.assertEqual(replies[0]["content"], "reply:urgent question 中文")
                self.assertFalse(proof.exists())
                self.assertIsNone(read_json(self.records.call(call["call_id"]) / "result.json"))
                gate.touch()
                self.assertEqual(self.result(call)["outcome"], "completed")
                self.assertEqual(proof.read_text(), "finished original assignment")

    def test_ordinary_message_eventually_arrives_without_cancelling_all_providers(self):
        for provider in BINARIES:
            with self.subTest(provider=provider):
                call, gate, proof = self.start(provider)
                sent = messages.send(self.records, call["agent_id"], "ordinary", sender=self.controller)
                if provider in {"codex", "muse", "opencode"}:
                    self.eventually(lambda: self.reply(sent))
                    self.assertFalse(proof.exists())
                gate.touch()
                self.assertEqual(self.result(call)["content"], "assignment complete")
                self.eventually(lambda: self.reply(sent))
                self.assertNotIn("cancelled", (self.root / ("events-" + call["agent_id"])).read_text())

    def test_selected_model_and_effort_survive_interrupt_and_idle_message_resume(self):
        call, gate, proof = self.start("codebuddy", model="native-model", effort="native-effort")
        sent = messages.send(self.records, call["agent_id"], "urgent", sender=self.controller, interrupt=True)
        self.eventually(lambda: self.reply(sent))
        self.assertFalse(proof.exists())
        gate.touch()
        self.assertEqual(self.result(call)["outcome"], "completed")
        self.eventually(lambda: messages.agent(self.records, call["agent_id"]).get("idle"))
        later = messages.send(self.records, call["agent_id"], "idle followup", sender=self.controller)
        self.eventually(lambda: self.reply(later))
        latest = self.eventually(lambda: (value if value.get("idle") else None) if (value := messages.agent(self.records, call["agent_id"])) else None)
        self.assertNotEqual(latest["call_id"], call["call_id"])
        task = self.records.require(latest["call_id"])[1]
        expected = {"model": "native-model", "effort": "native-effort"}
        self.assertEqual({key: task[key] for key in expected}, expected)
        trace = (self.root / ("events-" + call["agent_id"])).read_text().splitlines()
        turns = [json.loads(line.removeprefix("selection:")) for line in trace if line.startswith("selection:")]
        self.assertGreaterEqual(len(turns), 3)  # Original, urgent handling/continued assignment, idle mail.
        self.assertTrue(all(value == expected for value in turns))

    def test_idle_delivery_and_explicit_resume_preserve_address_and_old_result(self):
        call, gate, proof = self.start("cursor", name="reviewer")
        gate.touch()
        original = self.result(call)
        self.eventually(lambda: messages.agent(self.records, call["agent_id"]).get("idle"))
        sent = messages.send(self.records, "reviewer", "after completion", sender=self.controller)
        self.eventually(lambda: self.reply(sent))
        latest = self.eventually(lambda: (value if value.get("idle") else None) if (value := messages.agent(self.records, call["agent_id"])) else None)
        self.assertNotEqual(latest["call_id"], call["call_id"])
        latest_result = read_json(self.records.call(latest["call_id"]) / "result.json")
        self.assertEqual(latest_result["native_session_id"], original["native_session_id"])
        self.assertEqual(read_json(self.records.call(call["call_id"]) / "result.json"), original)
        continued = engine.resume(self.records, latest["call_id"], "continue", return_mode="wait")
        self.assertEqual(continued["agent_id"], call["agent_id"])
        self.assertEqual(self.result(continued)["outcome"], "completed")

    def test_parent_controls_peers_and_valid_reply_needs_no_reverse_grant(self):
        left, gate_l, _ = self.start(name="left", peers=["right"])
        right, gate_r, _ = self.start(name="right")
        sent = messages.send(self.records, "right", "peer question", sender=left["agent_id"], interrupt=True)
        self.eventually(lambda: messages.wait(self.records, sent["message_id"], sender=left["agent_id"], block=False))
        with self.assertRaises(SwarmError) as caught:
            messages.send(self.records, "left", "unsolicited", sender=right["agent_id"])
        self.assertEqual(caught.exception.code, "peer_not_allowed")
        with self.assertRaises(SwarmError):
            messages.reply(self.records, sent["message_id"], "spoof", sender=self.controller)
        outsider = "main_" + uuid.uuid4().hex
        write_json(messages.agent_path(self.records, outsider) / "agent.json", {"agent_id": outsider, "call_id": None, "owner_id": None})
        with self.assertRaises(SwarmError):
            messages.send(self.records, left["agent_id"], "wrong controller", sender=outsider)
        gate_l.touch()
        gate_r.touch()
        self.result(left)
        self.result(right)

    def test_cross_questions_wait_returns_incoming_without_deadlock(self):
        left, _, _ = self.start("cursor", name="left", peers=["right"])
        right, _, _ = self.start("cursor", name="right", peers=["left"])
        sent_l = messages.send(self.records, "right", "from left", sender=left["agent_id"])
        sent_r = messages.send(self.records, "left", "from right", sender=right["agent_id"])
        got_l = messages.wait(self.records, sent_l["message_id"], sender=left["agent_id"])
        got_r = messages.wait(self.records, sent_r["message_id"], sender=right["agent_id"])
        self.assertEqual(got_l[0]["message_id"], sent_r["message_id"])
        self.assertEqual(got_r[0]["message_id"], sent_l["message_id"])
        messages.reply(self.records, sent_r["message_id"], "answer left", sender=left["agent_id"])
        messages.reply(self.records, sent_l["message_id"], "answer right", sender=right["agent_id"])
        self.assertEqual(messages.wait(self.records, sent_l["message_id"], sender=left["agent_id"])[0]["content"], "answer right")
        self.assertEqual(messages.wait(self.records, sent_r["message_id"], sender=right["agent_id"])[0]["content"], "answer left")

    def test_live_input_failure_is_reported_to_parent_and_original_task_survives(self):
        config = self.records.config()
        config["providers"]["codex"]["command"].append("reject-insert")
        write_json(self.records.home / "config.json", config)
        call, gate, proof = self.start()
        sent = messages.send(self.records, call["agent_id"], "recover me", sender=self.controller, interrupt=True)
        events = self.eventually(lambda: messages.receive(self.records, self.controller))
        self.assertEqual(events[0]["kind"], "communication_issue")
        self.assertIn("fixture live input rejected", events[0]["content"])
        self.assertEqual(messages.inspect(self.records, sent["message_id"])["delivery"]["state"], "needs_attention")
        gate.touch()
        self.assertEqual(self.result(call)["outcome"], "completed")
        self.assertTrue(proof.exists())
        self.eventually(lambda: messages.agent(self.records, call["agent_id"]).get("idle"))
        messages.retry(self.records, sent["message_id"], sender=self.controller)
        self.eventually(lambda: self.reply(sent))

    def test_cancelled_recipient_is_not_restarted_and_mail_is_retained(self):
        call, _, _ = self.start()
        engine.cancel(self.records, call["call_id"])
        self.assertEqual(self.result(call)["outcome"], "cancelled")
        self.eventually(lambda: messages.agent(self.records, call["agent_id"]).get("idle"))
        sent = messages.send(self.records, call["agent_id"], "retained", sender=self.controller)
        self.assertEqual(sent["delivery"]["state"], "needs_attention")
        self.assertEqual(messages.inspect(self.records, sent["message_id"])["message"]["content"], "retained")
        self.assertEqual(len(list(self.records.calls())), 1)
        self.assertEqual(messages.receive(self.records, self.controller)[0]["kind"], "communication_issue")

    def test_interrupt_with_pending_permission_withdraws_old_request_and_keeps_new_approval(self):
        call, gate, proof = self.start("cursor", approval=True)
        first = self.eventually(lambda: approvals.pending(self.records, call["call_id"]))[0]
        sent = messages.send(self.records, call["agent_id"], "question while approval paused", sender=self.controller, interrupt=True)
        self.eventually(lambda: self.reply(sent))
        requests = self.eventually(lambda: approvals.pending(self.records, call["call_id"]))
        self.assertNotEqual(requests[0]["request_id"], first["request_id"])
        with self.assertRaises(SwarmError):
            approvals.decide(self.records, call["call_id"], first["request_id"], "allow")
        approvals.decide(self.records, call["call_id"], requests[0]["request_id"], "allow")
        gate.touch()
        self.assertEqual(self.result(call)["outcome"], "completed")
        self.assertTrue(proof.exists())

    def test_cli_controller_collects_messages_and_wait_returns_before_result(self):
        call, _, _ = self.start("cursor")
        messages.send(self.records, self.controller, "progress", sender=call["agent_id"])
        env = engine.runtime_env(self.records)
        for key in ("AUTOSWARM_CALL_ID", "CODEX_THREAD_ID", "AUTOSWARM_PARENT"):
            env.pop(key, None)
        done = subprocess.run([sys.executable, "-m", "autoswarm", "--controller", self.controller, "wait", call["call_id"]], env=env, text=True, capture_output=True, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["data"][0]["content"], "progress")

    def test_reply_wait_surfaces_child_approvals_instead_of_blocking(self):
        call, _, _ = self.start("cursor", approval=True)
        sent = messages.send(self.records, call["agent_id"], "ordinary", sender=self.controller)
        events = messages.wait(self.records, sent["message_id"], sender=self.controller)
        self.assertEqual(events[0]["kind"], "approval_required")

    def test_sender_with_restricted_process_visibility_does_not_discard_live_peer_mail(self):
        call, gate, _ = self.start()
        with patch("autoswarm.messages.platform.running", return_value=False):
            sent = messages.send(self.records, call["agent_id"], "hidden PID", sender=self.controller, interrupt=True)
        self.eventually(lambda: self.reply(sent))
        self.assertNotEqual(messages.inspect(self.records, sent["message_id"])["delivery"]["state"], "needs_attention")
        gate.touch()
        self.assertEqual(self.result(call)["outcome"], "completed")

    def test_queued_mail_survives_native_resume_without_changing_its_address(self):
        call, gate, _ = self.start("cursor")
        gate.touch()
        original = self.result(call)
        self.eventually(lambda: messages.agent(self.records, call["agent_id"]).get("idle"))
        with patch("autoswarm.messages.wake"):
            sent = messages.send(self.records, call["agent_id"], "retained across resume", sender=self.controller)
        continued = engine.resume(self.records, call["call_id"], "continue", return_mode="wait")
        self.eventually(lambda: self.reply(sent))
        self.assertEqual(continued["agent_id"], call["agent_id"])
        self.assertEqual(self.result(continued)["outcome"], "completed")
        self.assertEqual(read_json(self.records.call(call["call_id"]) / "result.json"), original)

    def test_wait_surfaces_other_owned_approvals_including_mail_followups(self):
        held, _, _ = self.start()
        approval_call, _, _ = self.start("cursor", approval=True)
        values = engine.wait(self.records, [held["call_id"]], controller=self.controller)
        self.assertEqual(values[0]["kind"], "approval_required")
        self.assertEqual(values[0]["call_id"], approval_call["call_id"])

    def test_internal_call_watcher_keeps_its_receipts_separate_from_the_main_inbox(self):
        call, _, _ = self.start("cursor")
        sent = messages.send(self.records, self.controller, "message for main", sender=call["agent_id"])
        self.assertEqual(engine.receive(self.records, [call["call_id"]], controller=self.controller, include_inbox=False), [])
        self.assertEqual(messages.receive(self.records, self.controller)[0]["message_id"], sent["message_id"])

    def test_exact_main_host_bridge_accepts_mail_without_blocking_the_sender(self):
        call, _, _ = self.start("cursor")
        sent = messages.send(self.records, self.controller, "question for main", sender=call["agent_id"], interrupt=True)
        path = messages.agent_path(self.records, self.controller) / "agent.json"
        write_json(path, {**read_json(path), "parent": {"host": "codex", "session_id": "original-main", "endpoint": "ws://original"}})
        with patch("autoswarm.hosts.deliver_codex", return_value={"state": "delivered", "mechanism": "codex_turn_steer"}) as callback:
            messages.deliver_to_host(self.records, sent["message_id"])
            parent, event, _ = callback.call_args.args
            self.assertEqual(parent["session_id"], "original-main")
            self.assertEqual(event["message_id"], sent["message_id"])
        self.assertEqual(messages.inspect(self.records, sent["message_id"])["delivery"]["state"], "submitted")

    def test_main_host_notification_failure_keeps_message_available_to_wait(self):
        call, _, _ = self.start("cursor")
        sent = messages.send(self.records, self.controller, "retained question", sender=call["agent_id"])
        path = messages.agent_path(self.records, self.controller) / "agent.json"
        write_json(path, {**read_json(path), "parent": {"host": "codex", "session_id": "original-main", "endpoint": "ws://original"}})
        with patch("autoswarm.hosts.deliver_codex", side_effect=OSError("host disconnected")):
            messages.deliver_to_host(self.records, sent["message_id"])
        events = engine.wait(self.records, [call["call_id"]], controller=self.controller)
        self.assertEqual(events[0]["content"], "retained question")

    def test_failed_idle_message_turn_can_be_recovered_and_retried_with_same_id(self):
        call, gate, _ = self.start("cursor")
        gate.touch()
        self.result(call)
        self.eventually(lambda: messages.agent(self.records, call["agent_id"]).get("idle"))
        config = self.records.config()
        config["providers"]["cursor"]["command"].append("reject-prompt")
        write_json(self.records.home / "config.json", config)
        sent = messages.send(self.records, call["agent_id"], "recover the original mail", sender=self.controller)
        self.eventually(lambda: messages.inspect(self.records, sent["message_id"])["delivery"]["state"] == "needs_attention")
        self.eventually(lambda: messages.agent(self.records, call["agent_id"]).get("idle"))
        failed = messages.agent(self.records, call["agent_id"])["call_id"]
        config["providers"]["cursor"]["command"].remove("reject-prompt")
        write_json(self.records.home / "config.json", config)
        continued = engine.resume(self.records, failed, "continue after fixing the transport", return_mode="wait")
        self.assertEqual(self.result(continued)["outcome"], "completed")
        self.eventually(lambda: messages.agent(self.records, call["agent_id"]).get("idle"))
        messages.retry(self.records, sent["message_id"], sender=self.controller)
        self.eventually(lambda: self.reply(sent))

    def test_post_task_message_rpc_failure_preserves_the_completed_original_result(self):
        config = self.records.config()
        config["providers"]["cursor"]["command"].append("reject-followup")
        write_json(self.records.home / "config.json", config)
        call, gate, proof = self.start("cursor")
        sent = messages.send(self.records, call["agent_id"], "ordinary followup", sender=self.controller)
        gate.touch()
        result = self.result(call)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["content"], "assignment complete")
        self.assertTrue(proof.exists())
        self.assertEqual(messages.inspect(self.records, sent["message_id"])["delivery"]["state"], "needs_attention")
