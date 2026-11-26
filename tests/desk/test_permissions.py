"""Desk permission modes: Follow or each agent's own modes, the per-conversation choice, workers in their own agent's choice."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import approvals, engine, permissions, platform
from autoswarm.desk import control
from autoswarm.records import Records, SwarmError, read_json, write_json
try:
    from autoswarm_test_support import cleanup_temporary
except ImportError:  # Run as tests.desk.<module> from the checkout.
    from tests.autoswarm_test_support import cleanup_temporary

FIXTURE = Path(__file__).parent.parent / "fixtures/provider.py"
ROOT = Path(__file__).resolve().parents[2]
CLEAN = {key: value for key, value in os.environ.items()
         if not key.startswith("AUTOSWARM_") and key not in {"CODEX_THREAD_ID", "CODEX_HOME"}}
DESK = {"claude-code": "acceptEdits", "codex": "full-access", "cursor": "run-everything"}


class ModeTests(unittest.TestCase):
    def test_every_agent_may_follow_its_own_settings(self):
        for provider in ["claude-code", "dsh", "nanobot", "muse"]:
            self.assertEqual(permissions.check(provider, "follow"), "follow")
            self.assertEqual(permissions.launch({"provider": provider, "permission": "follow"}), ([], {}, {}))
        with self.assertRaises(SwarmError) as raised:
            permissions.table({"codex": "acceptEdits"})
        self.assertEqual(raised.exception.code, "invalid_permission")
        with self.assertRaises(SwarmError):
            permissions.check("nanobot", "yolo")

    def test_auto_is_each_agents_own_reviewer_or_its_mode_without_questions(self):
        self.assertEqual(set(permissions.AUTO), set(permissions.MODES))
        for provider, mode in permissions.AUTO.items():
            self.assertIn(mode, permissions.MODES[provider], provider)

    def test_each_mode_maps_to_the_agents_own_switch(self):
        launch = lambda provider, mode, **extra: permissions.launch({"provider": provider, "permission": mode, **extra})
        self.assertEqual(launch("codebuddy", "auto"), (["--permission-mode", "auto"], {}, {}))
        self.assertEqual(launch("cursor", "run-everything"), (["--force"], {"mode": "agent"}, {}))
        self.assertEqual(launch("cursor", "allowlist"), ([], {"mode": "agent"}, {}))
        self.assertEqual(launch("kimi", "auto"), ([], {"mode": "auto"}, {}))
        self.assertEqual(launch("copilot", "allow-all"), (["--allow-all"], {"mode": "agent"}, {}))
        self.assertEqual(launch("copilot", "plan"), ([], {"mode": "plan"}, {}))
        self.assertEqual(launch("hermes", "yolo"), ([], {"mode": "dont_ask"}, {"HERMES_YOLO_MODE": "1"}))
        self.assertEqual(launch("hermes", "accept_edits"), ([], {"mode": "accept_edits"}, {}))
        self.assertEqual(launch("dsh", "danger-full-access"), ([], {}, {"DSH_PERMISSION_MODE": "danger-full-access"}))
        self.assertEqual(launch("grok", "always-approve"), (["--always-approve"], {}, {}))
        self.assertEqual(launch("gemini", "plan"), (["--approval-mode", "plan"], {}, {}))
        self.assertEqual(launch("gemini", "default"), (["--approval-mode", "default"], {}, {}))
        self.assertEqual(launch("gemini", "auto_edit"), (["--approval-mode", "auto_edit"], {}, {}))
        self.assertEqual(launch("gemini", "yolo"), (["--yolo"], {}, {}))
        # OpenCode 2 (ACP) takes one action for all permissions; the user's inline config stays.
        env = permissions.environment({"provider": "opencode", "permission": "allow", "native_transport": "acp"},
                                      {"OPENCODE_CONFIG_CONTENT": json.dumps({"model": "mine"})})
        self.assertEqual(json.loads(env["OPENCODE_CONFIG_CONTENT"]), {"model": "mine", "permission": "allow"})
        env = permissions.environment({"provider": "opencode", "permission": "ask", "native_transport": "http"}, {})
        self.assertEqual(json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"], {"edit": "ask", "bash": "ask", "webfetch": "ask"})
        self.assertNotIn("OPENCODE_PERMISSION", env)
        self.assertEqual(permissions.environment({"provider": "dsh", "permission": "follow"}, {"A": "1"}), {"A": "1"})

    def test_desk_lists_the_same_modes(self):
        source = (ROOT / "desktop/renderer/app.mjs").read_text(encoding="utf-8")
        listed = re.search(r"const PERMISSION_MODES = (\{.*?\n\});", source, re.S).group(1)
        self.assertEqual(re.findall(r"^  '([\w-]+)': \[", listed, re.M), list(permissions.MODES))
        for provider, modes in permissions.MODES.items():
            names = re.findall(r"\['([\w-]+)'", re.search(r"'" + provider + r"': \[(.*?)\]\]", listed, re.S).group(1))
            self.assertEqual(names, list(modes), provider)
        auto = re.search(r"const PERMISSION_AUTO = (\{.*?\});", source).group(1)
        self.assertEqual(dict(re.findall(r"'([\w-]+)': '([\w-]+)'", auto)), permissions.AUTO)


class DeskPermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="permissions-中文 ")
        self.root = Path(self.temp.name).resolve()
        self.trace = self.root / "trace.jsonl"
        self.records = Records(self.root / "records")
        write_json(self.records.home / "config.json", {"providers": {
            provider: {"command": [sys.executable, str(FIXTURE), provider, "--selection-trace=" + str(self.trace)]}
            for provider in ["claude-code", "codex", "cursor", "opencode", "kimi", "copilot", "hermes", "dsh", "codebuddy", "grok", "gemini"]}})
        self.patches = [patch.dict(os.environ, dict(CLEAN, PYTHONPATH=str(ROOT / "src")), clear=True)]
        for value in self.patches:
            value.start()
        self.controller = control.initialize(self.records)["controller_id"]

    def tearDown(self):
        calls = [task["call_id"] for _, task in self.records.calls()]
        for cid in calls:
            engine.cancel(self.records, cid)
        deadline = time.monotonic() + 10
        while any(platform.running(read_json(self.records.call(cid) / "handle.json")) for cid in calls) and time.monotonic() < deadline:
            time.sleep(.01)
        for value in reversed(self.patches):
            value.stop()
        cleanup_temporary(self.temp, deadline)

    def traced(self):
        return [json.loads(line) for line in self.trace.read_text(encoding="utf-8").splitlines()] if self.trace.exists() else []

    def task(self, info):
        return read_json(self.records.call(info["call_id"]) / "task.json")

    def start(self, provider, permission=None, prompt="work"):
        info = control.start(self.records, self.controller, prompt, provider=provider, workspace=str(self.root),
                             permission=permission, permissions=DESK)
        return self.task(info), info

    def test_each_agent_starts_in_its_own_mode(self):
        task, info = self.start("claude-code")
        engine.wait(self.records, [info["call_id"]])
        self.assertEqual(task["permission"], "acceptEdits", "The agent's Desk default")
        self.assertEqual(task["command"][-2:], ["--permission-mode", "acceptEdits"])
        task, info = self.start("codex", "approve-for-me")
        engine.wait(self.records, [info["call_id"]])
        started = next(value for value in self.traced() if value.get("method") == "thread/start")["params"]
        self.assertEqual({key: started[key] for key in ("sandbox", "approvalPolicy", "approvalsReviewer")},
                         {"sandbox": "workspace-write", "approvalPolicy": "on-request", "approvalsReviewer": "auto_review"})
        task, info = self.start("cursor")
        engine.wait(self.records, [info["call_id"]])
        self.assertEqual(task["command"][-2:], ["--force", "acp"], "Run Everything")
        self.assertFalse(any(value.get("method") == "session/set_mode" for value in self.traced()), "Already in Agent mode")
        task, info = self.start("cursor", "plan")
        engine.wait(self.records, [info["call_id"]])
        self.assertNotIn("--force", task["command"])
        self.assertEqual([value["params"]["modeId"] for value in self.traced() if value.get("method") == "session/set_mode"], ["plan"])
        with self.assertRaises(SwarmError) as raised:
            self.start("codex", "acceptEdits")
        self.assertEqual(raised.exception.code, "invalid_permission")

    def test_an_agent_without_a_desk_choice_follows_its_own_settings(self):
        before = len(self.traced())
        task, info = self.start("claude-code", "follow")
        engine.wait(self.records, [info["call_id"]])
        self.assertEqual(task["permission"], "follow")
        self.assertNotIn("--permission-mode", task["command"])
        task, info = self.start("kimi")  # Not in Desk's table
        engine.wait(self.records, [info["call_id"]])
        self.assertEqual(task["permission"], "follow")
        self.assertFalse(any(value.get("method") == "session/set_mode" for value in self.traced()[before:]))
        # Native permission flags are the user's own while Desk follows.
        info = engine.start_call(self.records, "copilot", "work", self.root, controller=task["agent_id"], provider_args=["--allow-all"])
        self.assertEqual(self.task(info)["permission"], "follow")
        self.assertIn("--allow-all", self.task(info)["command"], "A worker whose Desk choice is Follow keeps the user's flag")
        engine.wait(self.records, [info["call_id"]])

    def test_every_agent_starts_in_its_desk_mode(self):
        def run(provider, mode):
            before = len(self.traced())
            task, info = self.start(provider, mode)
            engine.wait(self.records, [info["call_id"]])
            return task, self.traced()[before:]
        modes = lambda trace: [value["params"]["modeId"] for value in trace if value.get("method") == "session/set_mode"]
        started = lambda trace: next(value for value in trace if "argv" in value)
        task, trace = run("kimi", "auto")
        self.assertEqual(modes(trace), ["auto"])
        task, trace = run("copilot", "plan")
        self.assertEqual(modes(trace), ["https://agentclientprotocol.com/protocol/session-modes#plan"], "Copilot names its modes by URL")
        task, trace = run("copilot", "allow-all")
        self.assertIn("--allow-all", started(trace)["argv"])
        self.assertEqual(modes(trace), [], "Already in Agent mode")
        task, trace = run("hermes", "yolo")
        self.assertEqual(modes(trace), ["dont_ask"])
        self.assertEqual(started(trace)["permission_env"], {"HERMES_YOLO_MODE": "1"})
        task, trace = run("dsh", "danger-full-access")
        self.assertEqual(started(trace)["permission_env"], {"DSH_PERMISSION_MODE": "danger-full-access"})
        task, trace = run("codebuddy", "auto")
        self.assertIn("--permission-mode", started(trace)["argv"])
        task, trace = run("grok", "always-approve")
        self.assertIn("--always-approve", started(trace)["argv"])

    def test_a_mode_an_updated_agent_no_longer_offers_does_not_stop_the_work(self):
        info = engine.start_call(self.records, "kimi", "work", self.root, controller=self.controller,
                                 permissions={}, permission="auto", provider_args=["no-modes"])
        self.assertEqual(engine.wait(self.records, [info["call_id"]])[0]["outcome"], "completed")
        self.assertEqual(read_json(self.records.call(info["call_id"]) / "handle.json")["permission_unavailable"], "auto",
                         "The call records that the agent's own mode was kept")

    def test_without_desk_permissions_agents_keep_their_own_settings(self):
        info = engine.start_call(self.records, "claude-code", "work", self.root, controller=self.controller)
        engine.wait(self.records, [info["call_id"]])
        task = self.task(info)
        self.assertNotIn("permission", task)
        self.assertNotIn("--permission-mode", task["command"])
        self.assertTrue(approvals.delegated(self.records, task))

    def test_a_conversation_change_starts_with_its_next_turn(self):
        task, info = self.start("claude-code", "default")
        engine.wait(self.records, [info["call_id"]])
        self.assertEqual(task["permission"], "default")
        self.assertEqual(control.permission(self.records, self.controller, task["agent_id"], "bypassPermissions")["permission"], "bypassPermissions")
        with self.assertRaises(SwarmError) as raised:
            control.permission(self.records, self.controller, task["agent_id"], "full-access")
        self.assertEqual(raised.exception.code, "invalid_permission")
        control.message(self.records, self.controller, task["agent_id"], "next")
        later = lambda: [t for _, t in self.records.calls() if t.get("previous_call") == task["call_id"]]
        deadline = time.monotonic() + 30
        while not later() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(later()[0]["permission"], "bypassPermissions")
        self.assertEqual(later()[0]["command"][-2:], ["--permission-mode", "bypassPermissions"])
        outside = engine.start_call(self.records, "codex", "work", self.root, controller=self.controller)
        with self.assertRaises(SwarmError) as raised:
            control.permission(self.records, self.controller, outside["agent_id"], "ask")
        self.assertEqual(raised.exception.code, "permission_unmanaged")

    def test_workers_use_their_own_agents_choice_and_their_starter_decides_their_requests(self):
        main, info = self.start("claude-code", "default", prompt="hold:" + str(self.root / "held"))
        # Desk's Codex default is Full access; a Manual main does not limit it.
        child = engine.start_call(self.records, "codex", "approval:" + str(self.root / "marker"), self.root, controller=main["agent_id"])
        worker = self.task(child)
        self.assertEqual(worker["permission"], "full-access")
        self.assertEqual(worker["permissions"], DESK)
        self.assertTrue(approvals.delegated(self.records, worker), "As everywhere in AutoSwarm, the starter decides first")
        deadline = time.monotonic() + 30
        while not approvals.pending(self.records, child["call_id"]) and time.monotonic() < deadline:
            time.sleep(.05)
        request = engine.receive(self.records, [child["call_id"]], controller=main["agent_id"])[0]
        self.assertEqual(request["kind"], "approval_required", "The main agent receives its worker's request")
        approvals.decide(self.records, child["call_id"], request["request_id"], "allow", actor="parent")
        self.assertEqual(engine.wait(self.records, [child["call_id"]])[0]["outcome"], "completed")
        # The user may change a worker's own mode in Desk.
        control.permission(self.records, self.controller, child["agent_id"], "read-only")
        again = engine.resume(self.records, child["call_id"], "again")
        self.assertEqual(self.task(again)["permission"], "read-only")
        engine.wait(self.records, [again["call_id"]])
        # A worker's worker uses its own agent's choice too, not its starter's.
        grandchild = engine.start_call(self.records, "cursor", "work", self.root, controller=child["agent_id"])
        self.assertEqual(self.task(grandchild)["permission"], "run-everything", "Not limited by the Read only worker")
        engine.wait(self.records, [grandchild["call_id"]])
        with self.assertRaises(SwarmError) as raised:
            engine.start_call(self.records, "cursor", "work", self.root, controller=main["agent_id"], provider_args=["--force"])
        self.assertEqual(raised.exception.code, "permission_managed")


    def test_a_command_without_its_call_id_still_belongs_to_the_conversation_running_it(self):
        # Codex's Windows sandbox runs an agent's commands as another account without
        # AUTOSWARM_CALL_ID; the native session it reports is the running conversation's.
        main, info = self.start("codex", "ask", prompt="hold:" + str(self.root / "held"))
        handle = self.records.call(info["call_id"]) / "handle.json"
        deadline = time.monotonic() + 30
        while not (read_json(handle) or {}).get("native_session_id") and time.monotonic() < deadline:
            time.sleep(.05)
        session = read_json(handle)["native_session_id"]
        with patch.dict(os.environ, {"AUTOSWARM_PARENT": json.dumps({"host": "codex", "session_id": session})}):
            os.environ.pop("AUTOSWARM_CALL_ID", None)
            child = engine.start_call(self.records, "claude-code", "work", self.root)
        self.assertEqual(self.task(child)["controller_id"], main["agent_id"])
        engine.wait(self.records, [child["call_id"]])
        # Once that conversation is no longer running, the session is a separate native conversation again.
        engine.cancel(self.records, info["call_id"])
        engine.wait(self.records, [info["call_id"]])
        with patch.dict(os.environ, {"AUTOSWARM_PARENT": json.dumps({"host": "codex", "session_id": session})}):
            later = engine.start_call(self.records, "claude-code", "work", self.root)
        self.assertTrue(self.task(later)["controller_id"].startswith("main_"))
        engine.wait(self.records, [later["call_id"]])

    def test_agents_are_not_started_from_another_account(self):
        # Inside Codex's Windows sandbox the main agent's commands run as a CodexSandbox* account; an
        # agent started there would lack the user's environment, logins and network.
        main, info = self.start("codex", "ask", prompt="hold:" + str(self.root / "held"))
        handle = self.records.call(info["call_id"]) / "handle.json"
        deadline = time.monotonic() + 30
        while not (read_json(handle) or {}).get("native_session_id") and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(read_json(handle)["account"], platform.account(), "a runner records its account")
        before = {task["call_id"] for _, task in self.records.calls()}
        with patch.object(platform, "account", return_value="CodexSandboxOffline"):
            with self.assertRaises(SwarmError) as raised:
                engine.start_call(self.records, "claude-code", "work", self.root, controller=main["agent_id"])
        self.assertEqual(raised.exception.code, "sandbox_account")
        self.assertIn("escalat", str(raised.exception))
        self.assertEqual({task["call_id"] for _, task in self.records.calls()}, before, "nothing is recorded for a refused start")
        # The same account starts it as always.
        child = engine.start_call(self.records, "claude-code", "work", self.root, controller=main["agent_id"])
        self.assertEqual(engine.wait(self.records, [child["call_id"]])[0]["outcome"], "completed")

    @unittest.skipUnless(os.name == "nt", "Windows permissions")
    def test_call_folders_inherit_the_records_folder_permissions_on_windows(self):
        # Owner-only folders made by another account (an agent's sandbox) would be unreadable to the user.
        # A call folder gets what any folder made there gets, including an entry granted on the records
        # folder; an owner-only one (mode 0o700) would lack it. Whether icacls marks inherited entries
        # "(I)" depends on the folders above (GitHub's Windows runners do not), so that mark is ignored.
        def entries(path):
            listing = subprocess.run(["icacls", path.name], cwd=path.parent, capture_output=True, text=True, errors="replace").stdout
            return {line.removeprefix(path.name).strip().replace("(I)", "") for line in listing.splitlines() if ":(" in line}
        calls = self.records.home / "calls"
        calls.mkdir(parents=True, exist_ok=True)
        before = entries(calls)
        subprocess.run(["icacls", calls.name, "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"], cwd=calls.parent, check=True, capture_output=True)
        granted = entries(calls) - before  # Read access for BUILTIN\Users, by SID so any language works.
        self.assertTrue(granted)
        info = engine.start_call(self.records, "claude-code", "work", self.root, controller=self.controller)
        engine.wait(self.records, [info["call_id"]])
        plain = calls / "plain"
        plain.mkdir()
        call = entries(self.records.call(info["call_id"]))
        self.assertLessEqual(granted, call, call)
        self.assertEqual(call, entries(plain))


if __name__ == "__main__":
    unittest.main()
