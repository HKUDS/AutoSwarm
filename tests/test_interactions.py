"""Native wire contracts, durable answers, cancellation and parent recovery."""
from __future__ import annotations

import io
import errno
import contextlib
import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autoswarm import approvals, engine, messages, platform
from autoswarm.cli import dispatch, parser
from autoswarm.hosts import result_message
from autoswarm.native import Native, Output
from autoswarm.records import Records, SwarmError, read_json, write_json


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.records = Records(self.root / "state")
        self.natives = []
        self.parent = None

    def tearDown(self):
        for n in self.natives:
            n.close()
        for n in self.natives:
            for t in n.broker.threads:
                t.join(3)  # Test deadline only; product has no reply deadline.
                self.assertFalse(t.is_alive())
        self.tmp.cleanup()

    def native(self, provider, **settings):
        task = {"call_id": "call_" + uuid.uuid4().hex, "provider": provider, "workspace": str(self.root),
                "prompt": "original assignment", "parent": None, "return_mode": "wait", **settings}
        controller = messages.prepare(self.records, task, controller=self.parent)
        self.parent = task["controller_id"]
        write_json(messages.agent_path(self.records, task["agent_id"]) / "agent.json", controller)
        directory = self.records.call(task["call_id"])
        write_json(directory / "task.json", task)
        write_json(directory / "handle.json", {"pid": os.getpid(), "identity": platform.identity(os.getpid()), "native_session_id": "native-session"})
        n = Native(self.records, task, SimpleNamespace(stdin=io.BytesIO()), Output(session_id="native-session"), lambda _: None)
        if n.adapter:
            n.adapter.accepting = True
        self.natives.append(n)
        return n

    def wire(self, n):
        return [json.loads(v) for v in n.process.stdin.getvalue().splitlines()]

    def test_closing_an_exited_peer_accepts_windows_pipe_einval(self):
        n = self.native("codex")
        class Pipe:
            closed = False
            def close(self):
                self.closed = True
                raise OSError(errno.EINVAL, "Native peer exited while closing stdin")
        n.process = SimpleNamespace(stdin=Pipe(), poll=lambda: 0)
        n.close()
        self.assertTrue(n.broker.closed.is_set())

    def test_live_peer_close_errors_remain_visible(self):
        n = self.native("codex")
        class Pipe:
            closed = False
            def close(self):
                self.closed = True
                raise OSError(errno.EIO, "Live native stdin failed")
        n.process = SimpleNamespace(stdin=Pipe(), poll=lambda: None)
        with self.assertRaises(OSError):
            n.end_input()

    def feed(self, n, method, params, request_id="native-request"):
        event = {"method": method, "params": params}
        if request_id is not None:
            event["id"] = request_id
        n.feed(json.dumps(event))
        return event

    def requests(self, n):
        return approvals.pending(self.records, n.task["call_id"])

    def request(self, n):
        values = self.requests(n)
        self.assertEqual(len(values), 1, values)
        return values[0]

    def answer(self, n, response, **kwargs):
        request = self.request(n)
        approvals.answer(self.records, n.task["call_id"], request["request_id"], response, **kwargs)
        return request

    def until(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Expected native event was not delivered")
            time.sleep(.01)

    def resolved(self, n, request):
        path = approvals.location(self.records, request["call_id"], request["request_id"])
        self.until(lambda: (path / "resolution.json").exists())
        return read_json(path / "resolution.json")

    def test_codex_answers_preserve_native_ids_and_unicode(self):
        n = self.native("codex")
        self.feed(n, "item/tool/requestUserInput", {"threadId": "native-session", "turnId": "turn", "itemId": "item", "isBlocking": False,
                   "questions": [{"id": "q", "question": "Which?", "options": []}]})
        request = self.request(n)
        self.assertFalse(request["native_request"]["isBlocking"])
        response = {"answers": {"q": {"answers": ["中文 🐝"]}}}
        self.answer(n, response)
        self.assertEqual(self.resolved(n, request)["state"], "replied")
        self.assertEqual(self.wire(n)[0], {"id": "native-request", "result": response})

    def test_claude_question_is_not_permission_and_keeps_original_input(self):
        n = self.native("claude-code")
        original = {"questions": [{"question": "选什么？", "multiSelect": True}], "metadata": {"keep": True}}
        n.feed(json.dumps({"type": "control_request", "request_id": "native-request", "request": {"subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": original}}))
        request = self.request(n)
        with self.assertRaises(SwarmError) as error:
            approvals.decide(self.records, request["call_id"], request["request_id"], "allow")
        self.assertEqual(error.exception.code, "answer_required")
        self.answer(n, {"answers": {"选什么？": ["A", "B"]}})
        self.resolved(n, request)
        response = self.wire(n)[0]["response"]["response"]
        self.assertEqual(response, {"behavior": "allow", "updatedInput": {**original, "answers": {"选什么？": ["A", "B"]}}})

    def test_claude_cancel_is_a_native_denial(self):
        n = self.native("claude-code")
        n.feed(json.dumps({"type": "control_request", "request_id": "q", "request": {"subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": []}}}))
        request = self.answer(n, {"action": "cancel"})
        self.resolved(n, request)
        self.assertEqual(self.wire(n)[0]["response"]["response"]["behavior"], "deny")

    def test_cursor_question_uses_option_ids_and_plan_has_own_response(self):
        n = self.native("cursor")
        self.feed(n, "cursor/ask_question", {"toolCallId": "tool", "questions": [{"id": "q", "prompt": "Which?", "options": [{"id": "id-a", "label": "A"}]}]})
        request = self.request(n)
        with self.assertRaises(SwarmError):
            self.answer(n, {"outcome": "answered", "answers": [{"questionId": "q", "selectedOptionIds": ["A"]}]})
        response = {"outcome": "answered", "answers": [{"questionId": "q", "selectedOptionIds": ["id-a"]}]}
        self.answer(n, response)
        self.resolved(n, request)
        self.assertEqual(self.wire(n)[0]["result"], response)
        self.feed(n, "cursor/create_plan", {"toolCallId": "plan", "plan": "review files"}, "plan-request")
        request = self.answer(n, {"outcome": "rejected", "reason": "Revise the plan"})
        self.resolved(n, request)
        self.assertEqual(self.wire(n)[1]["result"]["outcome"], "rejected")

    def test_opencode_http_answer_and_native_settlement_race(self):
        n = self.native("opencode")
        props = {"sessionID": "native-session", "id": "que-a", "questions": [{"question": "选什么？", "multiple": True}]}
        n.opencode_event({"type": "question.asked", "properties": props})
        calls = []
        def http(method, path, body):
            calls.append((method, path, body))
            n.opencode_event({"type": "question.replied", "properties": {"sessionID": "native-session", "requestID": "que-a"}})
            return True
        n.http = http
        request = self.answer(n, {"answers": [["A", "B"]]})
        self.resolved(n, request)
        self.assertEqual(calls, [("POST", "/question/que-a/reply", {"answers": [["A", "B"]]})])
        self.assertEqual(self.resolved(n, request)["state"], "resolved_by_native")
        with self.assertRaises(SwarmError):
            approvals.answer(self.records, request["call_id"], request["request_id"], {"answers": [["A"]]})

    def test_muse_receipt_duplicate_replay_and_ack_are_distinct(self):
        n = self.native("muse")
        params = {"sessionId": "native-session", "userInputId": "input", "questions": [{"id": "q", "options": [{"label": "A"}], "selection": {"mode": "single"}}]}
        self.feed(n, "userInput/request", params)
        self.feed(n, "userInput/requested", params, None)
        self.assertEqual(len(self.requests(n)), 1)
        self.assertEqual(self.wire(n), [{"jsonrpc": "2.0", "id": "native-request", "result": {}}])
        request = self.answer(n, {"answers": [{"questionId": "q", "selectedLabel": "A"}]})
        self.until(lambda: any(v.get("method") == "userInput/answer" for v in self.wire(n)))
        command = self.wire(n)[-1]
        self.assertEqual(uuid.UUID(command["params"]["commandId"]).version, 7)
        path = approvals.location(self.records, request["call_id"], request["request_id"])
        self.assertFalse((path / "resolution.json").exists())
        self.feed(n, "userInput/settled", {"sessionId": "native-session", "userInputId": "input"}, None)
        n.feed(json.dumps({"id": command["id"], "result": {"status": "accepted", "userInputId": "input"}}))
        self.assertEqual(self.resolved(n, request)["state"], "resolved_by_native")

    def test_muse_cancel_and_clarify_use_native_commands(self):
        for action, body in [("cancel", {"reason": "No answer"}), ("clarify", {"clarification": {"format": "text", "content": "Keep it local"}})]:
            n = self.native("muse")
            self.feed(n, "userInput/requested", {"sessionId": "native-session", "userInputId": "input", "questions": []}, None)
            request = self.answer(n, {"action": action, **body})
            self.until(lambda: len(self.wire(n)) == 2)
            command = self.wire(n)[-1]
            self.assertEqual(command["method"], "userInput/" + action)
            n.feed(json.dumps({"id": command["id"], "result": {"status": "accepted"}}))
            self.resolved(n, request)

    def test_codebuddy_answers_resolve_native_interruption_before_permission(self):
        n = self.native("codebuddy")
        self.feed(n, "session/request_permission", {"sessionId": "native-session", "toolCall": {"toolCallId": "tool", "_meta": {"codebuddy.ai/toolName": "AskUserQuestion"}, "rawInput": {"questions": [{"question": "Which?"}]}}, "options": [{"kind": "allow_once", "optionId": "yes"}]})
        request = self.answer(n, {"answers": {"Which?": "A"}})
        self.until(lambda: len(self.wire(n)) == 1)
        command = self.wire(n)[0]
        self.assertEqual(command["method"], "_codebuddy.ai/resolveInterruption")
        self.assertEqual(command["params"]["answers"], {"Which?": "A"})
        n.feed(json.dumps({"id": command["id"], "result": {"resolved": True, "queued": False}}))
        self.resolved(n, request)
        self.assertEqual(self.wire(n)[1]["result"], {"outcome": {"outcome": "cancelled"}})

    def test_native_error_retains_question_and_corrected_reply_without_stopping(self):
        n = self.native("muse")
        self.feed(n, "userInput/requested", {"sessionId": "native-session", "userInputId": "input", "questions": []}, None)
        request = self.answer(n, {"answers": []})
        self.until(lambda: len(self.wire(n)) == 2)
        n.feed(json.dumps({"id": self.wire(n)[-1]["id"], "error": {"code": -32057, "message": "Native schema changed"}}))
        self.until(lambda: bool(self.requests(n)))
        retry = self.request(n)
        self.assertIn("Native schema changed", retry["reply_error"]["error"])
        self.assertEqual(n.broker.errors, [])
        self.assertFalse(n.turn_done.is_set())
        # Reopening the request and publishing its diagnostic are separate
        # worker steps. Wait for the event without consuming its receipt.
        self.until(lambda: any(v.get("kind") == "communication_issue" for v in
                               engine.receive(self.records, [request["call_id"]], mark_received=False, controller=self.parent)))
        events = engine.receive(self.records, [request["call_id"]], controller=self.parent)
        self.assertTrue(any(v["kind"] == "communication_issue" for v in events))
        self.answer(n, {"action": "cancel"})
        self.until(lambda: self.wire(n)[-1].get("method") == "userInput/cancel")
        n.feed(json.dumps({"id": self.wire(n)[-1]["id"], "result": {"status": "accepted"}}))
        self.resolved(n, request)
        path = approvals.location(self.records, request["call_id"], request["request_id"])
        self.assertEqual(len(list((path / "attempts").glob("*.json"))), 2)

    def test_forms_require_user_review_and_validate_native_schema(self):
        for provider in ("codex", "claude-code", "cursor", "copilot", "codebuddy"):
            with self.subTest(provider=provider):
                n = self.native(provider)
                params = {"mode": "form", "message": "Project?", "requestedSchema": {"type": "object", "required": ["project"], "properties": {"project": {"type": "string", "enum": ["本地"]}}}}
                if provider == "codex":
                    self.feed(n, "mcpServer/elicitation/request", {"threadId": "native-session", **params})
                elif provider == "claude-code":
                    n.feed(json.dumps({"type": "control_request", "request_id": "native-request", "request": {"subtype": "elicitation", **params}}))
                else:
                    self.feed(n, "elicitation/create", {"sessionId": "native-session", **params})
                request = self.request(n)
                with self.assertRaises(SwarmError) as error:
                    self.answer(n, {"action": "accept", "content": {"project": "本地"}})
                self.assertEqual(error.exception.code, "user_answer_required")
                for content in ({}, {"project": False}, {"project": "wrong"}):
                    with self.assertRaises(SwarmError):
                        self.answer(n, {"action": "accept", "content": content}, actor="user")
                self.answer(n, {"action": "accept", "content": {"project": "本地"}}, actor="user")
                self.resolved(n, request)

    def test_url_completion_withdraws_pending_consent_without_opening_url(self):
        n = self.native("copilot")
        self.feed(n, "elicitation/create", {"mode": "url", "message": "Log in", "url": "https://example.test/authorize", "elicitationId": "auth"})
        request = self.request(n)
        self.assertTrue(request["user_required"])
        self.assertEqual(self.wire(n), [])
        self.feed(n, "elicitation/complete", {"elicitationId": "auth"}, None)
        with self.assertRaises(SwarmError):
            approvals.answer(self.records, request["call_id"], request["request_id"], {"action": "accept"}, actor="user")
        self.assertEqual(self.requests(n), [])
        n.adapter.interrupt()
        self.assertFalse(any("result" in v for v in self.wire(n)))

    def test_interrupt_answers_acp_questions_with_correct_cancel_shape(self):
        for method, body, cancellation in [("cursor/ask_question", {"questions": []}, {"outcome": "cancelled"}),
                                           ("elicitation/create", {"mode": "form"}, {"action": "cancel"})]:
            n = self.native("cursor")
            self.feed(n, method, body)
            request = self.request(n)
            n.adapter.interrupt()
            self.assertEqual(self.wire(n)[-1]["result"], cancellation)
            self.assertEqual(self.resolved(n, request)["state"], "interrupted_for_message")
            with self.assertRaises(SwarmError):
                approvals.answer(self.records, request["call_id"], request["request_id"], cancellation, actor="user")

    def test_cancel_and_turn_end_prevent_late_answers(self):
        for stop in ("cancel", "turn"):
            n = self.native("codex")
            self.feed(n, "item/tool/requestUserInput", {"threadId": "native-session", "questions": []})
            request = self.request(n)
            if stop == "cancel":
                write_json(self.records.call(request["call_id"]) / "cancel.json", {})
            else:
                n.finish_turn()
            with self.assertRaises(SwarmError):
                approvals.answer(self.records, request["call_id"], request["request_id"], {"answers": {}})
            self.assertEqual(self.wire(n), [])

    def test_wait_and_peer_wait_surface_questions_before_completion(self):
        n = self.native("codex")
        self.feed(n, "item/tool/requestUserInput", {"threadId": "native-session", "questions": []})
        request = self.request(n)
        self.assertIn(request, engine.wait(self.records, [request["call_id"]], controller=self.parent))
        with patch("autoswarm.messages.wake_message"):
            msg = messages.send(self.records, n.task["agent_id"], "question", sender=self.parent)
        values = messages.wait(self.records, msg["message_id"], sender=self.parent, block=False)
        self.assertTrue(any(v.get("kind") == "interaction_required" for v in values))
        self.assertIn("answer ", result_message(request, self.records.call(request["call_id"])))

    def test_cli_answer_file_and_delegation_policy(self):
        n = self.native("codex", delegate_approvals=False)
        self.feed(n, "item/tool/requestUserInput", {"threadId": "native-session", "questions": []})
        request = self.request(n)
        file = self.root / "answer.json"
        file.write_text('{"answers":{}}', encoding="utf-8")
        args = ["answer", request["call_id"], request["request_id"], "--input", str(file)]
        with self.assertRaises(SwarmError):
            dispatch(parser().parse_args(args), self.records)
        value = dispatch(parser().parse_args(args + ["--by", "user"]), self.records)
        self.assertEqual(value["response"], {"answers": {}})
        self.resolved(n, request)

    def test_unknown_required_interaction_reports_parent_and_retains_original(self):
        for provider in ("codex", "cursor", "copilot", "codebuddy", "muse", "claude-code"):
            n = self.native(provider)
            event = {"type": "control_request", "request_id": "unknown", "request": {"subtype": "request_user_dialog", "dialog_kind": "unknown", "payload": {"question": "Help?"}}} if provider == "claude-code" else {"id": "unknown", "method": "future/interaction", "params": {"sessionId": "native-session", "payload": "Retain me"}}
            n.feed(json.dumps(event))
            self.assertTrue(self.wire(n))
            path = self.records.call(n.task["call_id"])
            issues = list((path / "native-issues").glob("*.json"))
            self.assertEqual(read_json(issues[0])["native_request"], event)
            self.assertEqual(n.broker.errors, [])
            values = engine.receive(self.records, [n.task["call_id"]], controller=self.parent)
            self.assertTrue(any(v.get("kind") == "communication_issue" for v in values))
            self.assertFalse(n.turn_done.is_set())
            n.feed(json.dumps(event))
            self.assertEqual(len(list((path / "native-issues").glob("*.json"))), 1)

    def test_codex_async_question_returns_through_same_active_turn(self):
        n = self.native("codex")
        n.turn_id = "active-turn"
        self.feed(n, "item/completed", {"threadId": "native-session", "turnId": "active-turn", "item": {"id": "async-item", "type": "agentMessage", "delivery": "async", "text": "Choose scope", "questions": [{"title": "Which scope?", "options": ["Local", "Shared"]}]}}, None)
        request = self.request(n)
        self.assertEqual(request["response_format"], "codex-async-question")
        self.answer(n, {"text": "Local"})
        self.until(lambda: bool(self.wire(n)))
        command = self.wire(n)[0]
        self.assertEqual(command["method"], "turn/steer")
        self.assertEqual(command["params"]["expectedTurnId"], "active-turn")
        self.assertIn("Local", command["params"]["input"][0]["text"])
        n.feed(json.dumps({"id": command["id"], "result": {"turnId": "active-turn"}}))
        self.resolved(n, request)
        self.assertFalse(n.turn_done.is_set())

    def test_reused_rpc_id_in_a_new_turn_cannot_reuse_the_old_answer(self):
        n = self.native("codex")
        params = {"threadId": "native-session", "questions": []}
        self.feed(n, "item/tool/requestUserInput", params)
        old = self.request(n)
        n.finish_turn()
        n.continue_turn("continue original task")
        self.feed(n, "item/tool/requestUserInput", params)
        new = self.request(n)
        self.assertNotEqual(old["request_id"], new["request_id"])
        with self.assertRaises(SwarmError):
            approvals.answer(self.records, old["call_id"], old["request_id"], {"answers": {}})
        self.answer(n, {"answers": {}})
        self.resolved(n, new)
        self.assertEqual(len([v for v in self.wire(n) if v.get("id") == "native-request" and "result" in v]), 1)

    def test_muse_engages_only_when_parent_receives_the_question(self):
        n = self.native("muse")
        self.feed(n, "userInput/requested", {"sessionId": "native-session", "userInputId": "input", "questions": [], "autoResolutionMs": 60000}, None)
        request = self.request(n)
        time.sleep(.15)
        self.assertEqual(self.wire(n), [])
        engine.receive(self.records, [request["call_id"]], controller=self.parent)
        self.until(lambda: len(self.wire(n)) == 1)
        self.assertEqual(self.wire(n)[0]["method"], "userInput/engaged")
        self.assertEqual(len(self.requests(n)), 1)  # Receipt did not answer it.

    def test_codebuddy_interruption_notification_and_rpc_are_one_question(self):
        n = self.native("codebuddy")
        tool = {"toolCallId": "tool", "rawInput": {"questions": [{"question": "Which?"}]}, "_meta": {"codebuddy.ai/toolName": "AskUserQuestion"}}
        self.feed(n, "session/update", {"sessionId": "native-session", "update": {"sessionUpdate": "session_info_update", "_meta": {"codebuddy.ai/interruptionRequest": {"toolCallId": "tool", "toolName": "AskUserQuestion", "toolInput": tool["rawInput"]}}}}, None)
        first = self.request(n)
        self.feed(n, "session/request_permission", {"sessionId": "native-session", "toolCall": tool, "options": []})
        self.assertEqual(self.request(n)["request_id"], first["request_id"])
        self.answer(n, {"answers": {"Which?": "Local"}})
        self.until(lambda: len(self.wire(n)) == 1)
        command = self.wire(n)[0]
        n.feed(json.dumps({"id": command["id"], "result": {"resolved": True}}))
        self.resolved(n, first)
        self.assertEqual(len(self.wire(n)), 2)

    def test_duplicate_rpc_response_is_not_answered_or_reported_as_request(self):
        n = self.native("muse")
        n.request("test", {}, lambda _: None)
        response = {"id": self.wire(n)[0]["id"], "result": {}}
        n.feed(json.dumps(response))
        n.feed(json.dumps(response))
        self.assertEqual(len(self.wire(n)), 1)
        self.assertFalse((self.records.call(n.task["call_id"]) / "communication-error.json").exists())

    def test_pull_mode_native_issue_never_wakes_original_host(self):
        n = self.native("cursor")
        with patch("autoswarm.messages.subprocess.Popen") as launch:
            self.feed(n, "future/interaction", {})
            launch.assert_not_called()
        self.assertTrue(any(v.get("kind") == "communication_issue" for v in engine.receive(self.records, [n.task["call_id"]], controller=self.parent)))

    def test_malformed_answer_types_are_structured_errors_and_do_not_consume_request(self):
        n = self.native("cursor")
        self.feed(n, "cursor/create_plan", {"plan": "Review"})
        for response in ({"outcome": []}, {"outcome": {}}, []):
            with self.assertRaises(SwarmError) as error:
                self.answer(n, response)
            self.assertEqual(error.exception.code, "invalid_answer")
        self.assertEqual(len(self.requests(n)), 1)
        self.assertEqual(self.wire(n), [])

    def test_unknown_interactive_notification_and_malformed_request_are_retained(self):
        n = self.native("muse")
        self.feed(n, "userInput/futureRequested", {"sessionId": "native-session", "payload": "retain"}, None)
        self.feed(n, "userInput/request", {"sessionId": "native-session"})
        path = self.records.call(n.task["call_id"])
        self.assertEqual(len(list((path / "native-issues").glob("*.json"))), 2)
        self.assertEqual(self.wire(n)[0]["error"]["code"], -32601)
        self.assertFalse(n.turn_done.is_set())
        self.assertEqual(n.broker.errors, [])

    def test_reply_rejection_waits_for_a_concurrent_decision_writer(self):
        n = self.native("muse")
        self.feed(n, "userInput/requested", {"sessionId": "native-session", "userInputId": "input", "questions": []}, None)
        request = self.answer(n, {"answers": []})
        self.until(lambda: len(self.wire(n)) == 2)
        path = approvals.location(self.records, request["call_id"], request["request_id"])
        lease = platform.session_lease(path / "decision.lock")
        try:
            n.feed(json.dumps({"id": self.wire(n)[-1]["id"], "error": {"code": -32057, "message": "invalid"}}))
            self.until(lambda: any(read_json(p).get("state") == "reply_failed" for p in (path / "attempts").glob("*.json")))
            self.assertTrue((path / "decision.json").exists())
        finally:
            lease.close()
        self.until(lambda: bool(self.requests(n)))
        self.assertTrue(any(t.is_alive() for t in n.broker.threads))
        self.assertIn("reply_error", self.request(n))

    def test_claude_stop_hook_presents_native_question_and_allows_user_reply(self):
        from autoswarm.hooks import handle_hook
        n = self.native("cursor")
        n.task.update(return_mode="native", parent={"host": "claude-code", "mode": "hook", "session_id": "main-session"})
        write_json(self.records.call(n.task["call_id"]) / "task.json", n.task)
        self.feed(n, "cursor/create_plan", {"plan": "Review files"})
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"session_id":"main-session"}')), contextlib.redirect_stdout(output):
            self.assertEqual(handle_hook(self.records, "stop"), 0)
        self.assertIn("interaction required", json.loads(output.getvalue())["reason"])
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"session_id":"main-session"}')), contextlib.redirect_stdout(output):
            self.assertEqual(handle_hook(self.records, "stop"), 0)
        self.assertEqual(output.getvalue(), "")

    def test_failed_permission_reply_can_be_corrected_without_losing_native_request(self):
        n = self.native("cursor")
        self.feed(n, "session/request_permission", {"sessionId": "native-session", "toolCall": {"toolCallId": "tool"}, "options": [{"kind": "allow_always", "optionId": "always"}, {"kind": "reject_once", "optionId": "no"}]})
        request = self.request(n)
        approvals.decide(self.records, request["call_id"], request["request_id"], "allow")
        self.until(lambda: bool(self.requests(n)))
        self.assertIn("reply_error", self.request(n))
        self.assertEqual(self.wire(n), [])
        approvals.decide(self.records, request["call_id"], request["request_id"], "deny")
        self.resolved(n, request)
        self.assertEqual(self.wire(n)[0]["result"], {"outcome": {"outcome": "selected", "optionId": "no"}})

    def test_withdrawal_while_waiting_for_native_write_never_sends_late_answer(self):
        n = self.native("codex")
        self.feed(n, "item/tool/requestUserInput", {"threadId": "native-session", "questions": []})
        request = self.request(n)
        path = approvals.location(self.records, request["call_id"], request["request_id"])
        n.write_lock.acquire()
        try:
            self.answer(n, {"answers": {}})
            self.until(lambda: bool(list((path / "attempts").glob("*.json"))))
            n.broker.withdraw(request["native_request_id"], "resolved_by_native")
        finally:
            n.write_lock.release()
        self.until(lambda: any(read_json(p).get("state") == "settled_by_native" for p in (path / "attempts").glob("*.json")))
        self.assertEqual(self.wire(n), [])
        self.assertEqual(n.broker.errors, [])

    def test_startup_elicitation_can_settle_after_execution_turn_starts(self):
        n = self.native("copilot")
        self.feed(n, "elicitation/create", {"mode": "url", "elicitationId": "startup-auth", "url": "https://example.test/authorize"})
        request = self.request(n)
        n.broker.new_turn()
        self.feed(n, "elicitation/complete", {"elicitationId": "startup-auth"}, None)
        self.assertEqual(self.resolved(n, request)["state"], "resolved_by_native")
        self.assertEqual(self.requests(n), [])

    def test_capabilities_only_advertise_implemented_bridges(self):
        for provider in ("cursor", "copilot", "codebuddy", "muse"):
            n = self.native(provider)
            n.start()
            params = self.wire(n)[0]["params"]
            if provider == "muse":
                self.assertTrue(params["capabilities"]["userInputDialogs"])
            else:
                self.assertEqual(params["clientCapabilities"]["elicitation"], {"form": {}, "url": {}})
                self.assertFalse(params["clientCapabilities"]["terminal"])


if __name__ == "__main__":
    unittest.main()
