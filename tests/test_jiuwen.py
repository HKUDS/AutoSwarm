"""JiuwenSwarm through AutoSwarm's own bridge: the real bridge, with jiuwenswarm-process and its Gateway as fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from autoswarm import approvals, engine, models, permissions, platform, skills
from autoswarm.jiuwen import bridge_command
from autoswarm.providers import canonical, discover, invocation
from autoswarm.records import Records, SwarmError, read_json, write_json

try:
    from .autoswarm_test_support import cleanup_temporary
except ImportError:
    from autoswarm_test_support import cleanup_temporary

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROCESS = FIXTURES / "jiuwen_process.py"
GATEWAY = FIXTURES / "jiuwen_gateway"


class JiuwenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "jiuwen-state"
        self.workspace = self.root / "工作区 ws"
        self.workspace.mkdir()
        # The bridge imports the Gateway fixture as its websockets package (team mode).
        path = str(GATEWAY) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
        self.env = patch.dict(os.environ, {"JIUWEN_FIXTURE_STATE": str(self.state), "PYTHONPATH": path})
        self.env.start()
        os.environ.pop("JIUWEN_FIXTURE_GATEWAY", None)
        self.records = Records(self.root / "autoswarm")
        self.config = {"providers": {"jiuwen": {"command": [sys.executable, str(PROCESS)], "python": [sys.executable]}}}
        write_json(self.records.home / "config.json", self.config)
        self.calls = []

    def tearDown(self):
        for call in self.calls:
            engine.cancel(self.records, call["call_id"])
        end = time.monotonic() + 15  # Fixture cleanup only.
        while any(platform.running(read_json(self.records.call(c["call_id"]) / "handle.json")) for c in self.calls):
            if time.monotonic() > end:
                self.fail("JiuwenSwarm bridge did not exit")
            time.sleep(0.01)
        self.env.stop()
        cleanup_temporary(self.tmp, end)

    def start(self, text, **options):
        call = engine.start_call(self.records, "jiuwen", text, self.workspace, return_mode="wait", **options)
        self.calls.append(call)
        return call

    def final(self, call):
        return next(v for v in engine.wait(self.records, [call["call_id"]], controller=call["controller_id"]) if v.get("outcome"))

    def pending(self, call):
        value = engine.wait(self.records, [call["call_id"]], controller=call["controller_id"])[0]
        self.assertIn(value["kind"], {"approval_required", "interaction_required"}, value)
        return value

    def trace(self, name="trace.jsonl"):
        path = self.state / name
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def runs(self):
        return [entry for entry in self.trace() if "run" in entry]

    def test_one_agent_turn_then_resume_continues_jiuwens_own_session(self):
        first = self.final(self.start("hello 中文 🐝"))
        self.assertEqual(first["outcome"], "completed", first)
        self.assertEqual(first["content"], "jiuwen: hello 中文 🐝")
        self.assertTrue(first["native_session_id"].startswith("process_cli_fixture_"), "AutoSwarm keeps the session JiuwenSwarm named")
        run = self.runs()[0]
        self.assertEqual(run["run"]["mode"], "agent.code.normal")
        self.assertNotIn("session_id", run["run"])
        self.assertEqual(Path(run["cwd"]), self.workspace)
        self.assertEqual(run["run"]["workspace"]["project_dir"], str(self.workspace))
        resumed = engine.resume(self.records, first["call_id"], "recall", return_mode="wait")
        self.calls.append(resumed)
        second = self.final(resumed)
        self.assertEqual(second["content"], "first: hello 中文 🐝")
        self.assertEqual(second["native_session_id"], first["native_session_id"])
        run = self.runs()[1]
        self.assertEqual(run["run"]["session_id"], first["native_session_id"])
        self.assertNotIn("mode", run["run"], "a continued session keeps its own mode")

    def test_a_permission_card_waits_for_a_decision_that_reaches_jiuwen(self):
        for decision, reply, written in (("allow", "allowed", True), ("deny", "not allowed", False)):
            with self.subTest(decision=decision):
                name = decision + ".txt"
                call = self.start("ask:" + name)
                request = self.pending(call)
                self.assertEqual(request["kind"], "approval_required")
                native = read_json(next((self.records.call(call["call_id"]) / "approvals").glob("*/request.json")))["native_request"]
                self.assertIn("write_file", native["toolCall"]["title"])
                self.assertEqual(native["toolCall"]["rawInput"]["file_path"], name)
                self.assertEqual([o["optionId"] for o in native["options"]], ["allow_once", "session_allow", "reject"], "a grant for every session is never offered")
                self.assertFalse((self.workspace / name).exists())
                approvals.decide(self.records, call["call_id"], request["request_id"], decision)
                result = self.final(call)
                self.assertEqual(result["content"], reply + (" " + name if written else ""))
                self.assertEqual((self.workspace / name).exists(), written)
                answer = [e["control"] for e in self.trace() if "control" in e][-1]
                self.assertEqual(answer["answers"][0]["selected_options"], ["allow_once" if written else "reject"])

    def test_full_access_allows_the_tools_jiuwens_config_asks_for(self):
        self.assertEqual(permissions.launch({"provider": "jiuwen", "permission": "full-access"}), (["--permission", "full-access"], {}, {}))
        self.assertEqual(permissions.AUTO["jiuwen"], "full-access")
        result = self.final(self.start("ask:free.txt", provider_args=["--permission=full-access"]))
        self.assertEqual(result["content"], "allowed free.txt")
        self.assertTrue((self.workspace / "free.txt").exists())
        tools = self.runs()[0]["run"]["permissions"]["tools"]
        self.assertEqual(tools["write_file"], "allow")
        self.assertEqual(tools["fixture_tool"], "allow", "the list its own permission.get reports")
        self.assertNotIn("read_file", tools, "tools it already allows are left alone")

    def test_a_single_choice_question_is_answered_through_autoswarm(self):
        call = self.start("question")
        request = self.pending(call)
        self.assertEqual(request["kind"], "interaction_required")
        approvals.answer(self.records, call["call_id"], request["request_id"], {"optionId": "blue"})
        self.assertEqual(self.final(call)["content"], "chose blue")

    def test_cancel_stops_the_run_and_a_failure_says_why(self):
        call = self.start("hold")
        end = time.monotonic() + 20
        while not self.runs():
            self.assertLess(time.monotonic(), end, "the run did not start")
            time.sleep(0.05)
        engine.cancel(self.records, call["call_id"])
        self.assertEqual(self.final(call)["outcome"], "cancelled")
        failed = self.final(self.start("fail"))
        self.assertEqual(failed["outcome"], "failed")
        self.assertIn("MODEL_ERROR", failed["error"])

    def test_team_round_runs_in_the_service_and_continues_there(self):
        result = self.final(self.start("team work 中文", provider_args=["--mode=team"]))
        self.assertEqual(result["outcome"], "completed", result)
        self.assertEqual(result["content"], "team: team work 中文")
        self.assertTrue(result["native_session_id"].startswith("autoswarm-team-"))
        frames = self.trace("gateway.jsonl")
        self.assertIsNone(frames[0]["proxy"], "the local service is never reached through a system proxy")
        sent = [f["sent"] for f in frames if "sent" in f]
        self.assertEqual(sent[0]["method"], "chat.send")
        params = sent[0]["params"]
        self.assertEqual((params["mode"], params["session_id"], params["cwd"]), ("team.code.normal", result["native_session_id"], str(self.workspace)))
        self.assertTrue(params["supports_user_interaction"])
        resumed = engine.resume(self.records, result["call_id"], "again", return_mode="wait")
        self.calls.append(resumed)
        self.assertEqual(self.final(resumed)["content"], "team: again")
        self.assertEqual([f["sent"]["params"]["session_id"] for f in self.trace("gateway.jsonl") if "sent" in f][-1], result["native_session_id"])

    def test_team_permission_card_is_relayed_and_resumes_the_paused_round(self):
        call = self.start("ask", provider_args=["--mode=team"])
        request = self.pending(call)
        approvals.decide(self.records, call["call_id"], request["request_id"], "allow")
        self.assertEqual(self.final(call)["content"], "approved")
        answer = [f["sent"] for f in self.trace("gateway.jsonl") if "sent" in f][-1]
        self.assertEqual((answer["method"], answer["params"]["source"], answer["params"]["request_id"]), ("chat.send", "permission_interrupt", "req-1"))
        self.assertEqual(answer["params"]["answers"], [{"selected_options": ["allow_once"], "custom_input": "", "card_id": "card-1"}])

    def test_team_round_ends_after_the_leader_replies_without_a_closing_status(self):
        result = self.final(self.start("idle", provider_args=["--mode=team"]))
        self.assertEqual((result["outcome"], result["content"]), ("completed", "team: idle"))

    def test_team_cancel_and_a_service_that_is_not_running(self):
        call = self.start("hold", provider_args=["--mode=team"])
        end = time.monotonic() + 20
        while not any("sent" in f for f in self.trace("gateway.jsonl")):
            self.assertLess(time.monotonic(), end, "the round did not start")
            time.sleep(0.05)
        engine.cancel(self.records, call["call_id"])
        self.assertEqual(self.final(call)["outcome"], "cancelled")
        os.environ["JIUWEN_FIXTURE_GATEWAY"] = "down"
        failed = self.final(self.start("team work", provider_args=["--mode=team"]))
        self.assertEqual(failed["outcome"], "failed")
        self.assertIn("jiuwenswarm-start app", failed["error"])

    def test_a_jiuwenswarm_without_the_machine_protocol_is_reported_as_too_old(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("jiuwen_bridge_under_test", Path(engine.__file__).with_name("jiuwen_bridge.py"))
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        for found, problem in (("0.2.7", True), ("0.2.3", True), ("0.2.8", False), ("0.2.9b3", False), ("unknown", False)):
            with self.subTest(version=found), patch.object(bridge, "version", return_value=found):
                self.assertEqual(bool(bridge.outdated()), problem)
        with patch.object(bridge, "version", return_value="0.2.7"):
            self.assertIn("uv tool upgrade workswarm", bridge.outdated())

    def test_discovery_models_aliases_and_unsupported_choices(self):
        found = discover(self.config, ["jiuwen"])[0]
        self.assertTrue(found["available"], found)
        self.assertTrue(found["version"].startswith("JiuwenSwarm "))
        catalog = models.catalog(self.config, "jiuwen")
        self.assertEqual(catalog["models"], [{"id": "fixture-model#0", "label": "Fixture Model", "default": True, "efforts": []}])
        self.assertEqual([canonical(name) for name in ("jiuwenswarm", "workswarm", "openjiuwen")], ["jiuwen"] * 3)
        with self.assertRaises(SwarmError):
            invocation({"provider": "jiuwen", "workspace": str(self.workspace), "prompt": "", "effort": "high"}, self.config)
        argv = invocation({"provider": "jiuwen", "workspace": str(self.workspace), "prompt": "", "model": "fixture-model#0"}, self.config)
        self.assertEqual(argv[-2:], ["--model", "fixture-model#0"])
        self.assertEqual(bridge_command(["C:/jiuwen/jiuwenswarm-process.exe", "-x"], self.config)[-2:],
                         ["--process=C:/jiuwen/jiuwenswarm-process.exe", "--process=-x"])
        with patch.dict(os.environ, {"JIUWENSWARM_DATA_DIR": str(self.root / "data")}):
            self.assertEqual(skills.destination("jiuwen"), self.root / "data/agent/workspace/skills/autoswarm")


if __name__ == "__main__":
    unittest.main()
