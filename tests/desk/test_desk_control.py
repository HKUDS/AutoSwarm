"""Desk CLI uses native calls, stable ownership and existing session locking."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import engine, messages, platform
from autoswarm.cli import parse_args
from autoswarm.desk import control
from autoswarm.records import Records, SwarmError, read_json, write_json
try:
    from autoswarm_test_support import cleanup_temporary
except ImportError:  # Run as tests.desk.<module> from the checkout.
    from tests.autoswarm_test_support import cleanup_temporary


FIXTURE = Path(__file__).parent.parent / "fixtures/provider.py"


class DeskControlTests(unittest.TestCase):
    def test_user_can_continue_main_and_child_without_changing_owners(self):
        desk = control.initialize(self.records)["controller_id"]
        main = engine.start_call(self.records, "codex", "main assignment", self.root, return_mode="wait")
        engine.wait(self.records, [main["call_id"]])
        child = engine.start_call(self.records, "codex", "child assignment", self.root, controller=main["agent_id"], return_mode="wait")
        engine.wait(self.records, [child["call_id"]])
        for original in [main, child]:
            owner = messages.agent(self.records, original["agent_id"])["owner_id"]
            value = control.message(self.records, desk, original["agent_id"], "HUMAN_DIRECT_REPLY")
            current = messages.agent(self.records, original["agent_id"])
            result = engine.wait(self.records, [current["call_id"]])[0]
            self.assertEqual(current["owner_id"], owner)
            self.assertNotEqual(current["call_id"], original["call_id"])
            self.assertIn("HUMAN_DIRECT_REPLY", result["content"])
            self.assertEqual(result["native_session_id"], "fixture-session")
            self.assertEqual(value["message"]["kind"], "user_message")

    def test_after_turn_input_waits_for_boundary_and_keeps_human_answer(self):
        desk = control.initialize(self.records)["controller_id"]
        gate = self.root / "release"
        call = engine.start_call(self.records, "codex", "gate:" + str(gate) + "\nORIGINAL_ASSIGNMENT", self.root, return_mode="wait")
        sent = control.message(self.records, desk, call["agent_id"], "HUMAN_NEXT_TURN", mode="after_turn")
        time.sleep(.1)
        self.assertEqual(messages.inspect(self.records, sent["message"]["message_id"])["delivery"]["state"], "queued")
        gate.write_text("go")
        result = engine.wait(self.records, [call["call_id"]])[0]
        self.assertIn("HUMAN_NEXT_TURN", result["content"])
        self.assertNotIn("ORIGINAL_ASSIGNMENT", result["content"])
        self.assertEqual(messages.agent(self.records, call["agent_id"])["call_id"], call["call_id"])

    def test_external_main_queues_while_busy_then_resumes_exact_native_id(self):
        desk = control.initialize(self.records)["controller_id"]
        target = "main_" + "e" * 32
        write_json(messages.agent_path(self.records, target) / "agent.json", {"agent_id": target, "call_id": None, "owner_id": None,
                   "parent": {"host": "codex", "session_id": "exact-existing-thread"}})
        with patch("autoswarm.desk.native_history.conversation", return_value={"busy": True, "workspace": str(self.root)}):
            value = control.message(self.records, desk, target, "EXTERNAL_HUMAN_INPUT")
            self.assertEqual(value["delivery"]["state"], "queued")
            self.assertIsNone(messages.agent(self.records, target)["call_id"])
        with patch("autoswarm.desk.native_history.conversation", return_value={"busy": False, "workspace": str(self.root)}):
            control.flush(self.records, desk)
        call_id = messages.agent(self.records, target)["call_id"]
        _, task = self.records.require(call_id)
        self.assertEqual(task["native_session_id"], "exact-existing-thread")
        self.assertEqual(task["agent_id"], target)
        self.assertIn("EXTERNAL_HUMAN_INPUT", engine.wait(self.records, [call_id])[0]["content"])
        with patch("autoswarm.desk.native_history.conversation", return_value={"busy": False, "workspace": str(self.root)}):
            control.flush(self.records, desk)
        self.assertEqual(messages.agent(self.records, target)["call_id"], call_id)

    def test_agent_cannot_impersonate_direct_desk_user(self):
        desk = control.initialize(self.records)["controller_id"]
        with patch.dict(os.environ, {"AUTOSWARM_CALL_ID": "call_" + "f" * 32}):
            with self.assertRaises(SwarmError) as raised:
                control.message(self.records, desk, "main_" + "e" * 32, "pretend user")
            self.assertEqual(raised.exception.code, "desk_context_conflict")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="desk-controller-中文-")
        self.root = Path(self.temp.name).resolve()
        self.records = Records(self.root / "records")
        write_json(self.records.home / "config.json", {"providers": {
            provider: {"command": [sys.executable, str(FIXTURE), provider]}
            for provider in ["claude-code", "codex"]}})
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("AUTOSWARM_") and key != "CODEX_THREAD_ID"}
        self.environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        self.env_patch = patch.dict(os.environ, self.environment, clear=True)
        self.env_patch.start()

    def tearDown(self):
        calls = [task["call_id"] for _, task in self.records.calls()]
        for cid in calls:
            engine.cancel(self.records, cid)
        deadline = time.monotonic() + 10
        while any(platform.running(read_json(self.records.call(cid) / "handle.json")) for cid in calls) and time.monotonic() < deadline:
            time.sleep(.01)
        cleanup_temporary(self.temp, deadline)
        self.env_patch.stop()

    def cli(self, *args, prompt=None):
        result = subprocess.run([sys.executable, "-m", "autoswarm", "--home", str(self.records.home), *args],
                                env=self.environment, input=prompt, text=True, encoding="utf8", capture_output=True, timeout=20)
        return result.returncode, json.loads(result.stdout)

    def fingerprint(self):
        return {str(p.relative_to(self.records.home)): (p.stat().st_mtime_ns, p.read_bytes())
                for p in self.records.home.rglob('*') if p.is_file()}

    def test_init_is_stable_and_status_is_read_only(self):
        first = control.initialize(self.records)
        self.assertTrue(first["controller_id"].startswith("main_"))
        self.assertIsNone(first["assistant_agent_id"])
        self.assertEqual(control.initialize(self.records), first)
        self.assertEqual(control.initialize(self.records, first["controller_id"]), first)
        before = self.fingerprint()
        self.assertEqual(control.status(self.records), first)
        self.assertEqual(self.fingerprint(), before)

    def test_desk_refuses_unrelated_or_invalid_controller_without_overwriting(self):
        cid = "main_" + "b" * 32
        path = messages.agent_path(self.records, cid) / "agent.json"
        original = {"agent_id": cid, "owner_id": None, "call_id": None, "parent": {"host": "codex"}}
        write_json(path, original)
        before = self.fingerprint()
        for candidate in [cid, "../../elsewhere", "call_" + "b" * 32]:
            with self.subTest(candidate=candidate):
                with self.assertRaises(SwarmError) as raised:
                    control.initialize(self.records, candidate)
                self.assertEqual(raised.exception.code, "invalid_desk_controller")
        self.assertEqual(self.fingerprint(), before)

    def test_status_on_uninitialized_home_never_creates_records(self):
        records = Records(self.root / 'absent')
        with self.assertRaises(SwarmError) as raised:
            control.status(records)
        self.assertEqual(raised.exception.code, 'desk_not_initialized')
        self.assertFalse(records.home.exists())

    def test_cli_first_message_and_resume_keep_agent_and_raw_user_prompt(self):
        code, value = self.cli('desk', 'init')
        self.assertEqual(code, 0, value)
        controller = value['data']['controller_id']
        prompt = '用户原话：你好 🐝\n不要替我改写'
        code, value = self.cli('--controller', controller, 'desk', 'assistant', '--cwd', str(self.root), '--input', '-', prompt=prompt)
        self.assertEqual(code, 0, value)
        first = value['data']
        result = engine.wait(self.records, [first['call_id']])[0]
        self.assertEqual(result['outcome'], 'completed')
        self.assertEqual(first['controller_id'], controller)
        self.assertEqual(first['provider'], 'claude-code')
        self.assertEqual(first['desk']['assistant_agent_id'], first['agent_id'])
        task = read_json(self.records.call(first['call_id']) / 'task.json')
        self.assertEqual(task['prompt'], prompt)
        self.assertFalse(task['delegate_approvals'])
        self.assertIn('private assistant (Meta Agent)', task['communication']['rules'])
        self.assertIn(str(self.records.home).replace('\\', '\\\\'), task['communication']['rules'])
        self.assertIn('user_required', task['communication']['rules'])
        self.assertIn('private assistant (Meta Agent)', result['content'])
        code, value = self.cli('--controller', controller, 'desk', 'assistant', '--input', '-', prompt='下一轮')
        self.assertEqual(code, 0, value)
        second = value['data']
        engine.wait(self.records, [second['call_id']])
        self.assertEqual(first['agent_id'], second['agent_id'])
        self.assertNotEqual(first['call_id'], second['call_id'])
        second_task = read_json(self.records.call(second['call_id']) / 'task.json')
        self.assertEqual(second_task['previous_call'], first['call_id'])
        self.assertEqual(second_task['prompt'], '下一轮')
        self.assertEqual(second_task['workspace'], str(self.root))
        self.assertEqual(control.status(self.records, controller)['assistant_call_id'], second['call_id'])

    def test_running_assistant_rejects_another_turn_without_duplicate_agent(self):
        controller = control.initialize(self.records)['controller_id']
        gate = self.root / 'gate'
        first = control.assistant(self.records, controller, f'gate:{gate}\nWait for test release', workspace=str(self.root))
        with self.assertRaises(SwarmError) as raised:
            control.assistant(self.records, controller, 'second message')
        self.assertEqual(raised.exception.code, 'session_busy')
        self.assertEqual(len(list(self.records.calls())), 1)
        gate.touch()
        self.assertEqual(engine.wait(self.records, [first['call_id']])[0]['outcome'], 'completed')

    def test_first_launch_is_protected_by_controller_lease(self):
        controller = control.initialize(self.records)['controller_id']
        with platform.session_lease(messages.agent_path(self.records, controller) / 'assistant.lock'):
            with self.assertRaises(SwarmError) as raised:
                control.assistant(self.records, controller, 'hello')
            self.assertEqual(raised.exception.code, 'session_busy')
        self.assertEqual(list(self.records.calls()), [])

    def test_the_assistant_changes_agent_and_keeps_each_conversation(self):
        controller = control.initialize(self.records)['controller_id']
        first = control.assistant(self.records, controller, 'Plan the release notes\nwith details', provider='codex', workspace=str(self.root))
        engine.wait(self.records, [first['call_id']])
        with self.assertRaises(SwarmError) as raised:
            control.assistant(self.records, controller, 'continued', provider='codex', workspace=str(self.records.home))
        self.assertEqual(raised.exception.code, 'assistant_workspace_mismatch', "an agent's assistant conversation keeps its workspace")
        # Another agent: its own assistant conversation, told about the first one.
        second = control.assistant(self.records, controller, 'hello', provider='claude-code', workspace=str(self.root))
        engine.wait(self.records, [second['call_id']])
        self.assertNotEqual(second['agent_id'], first['agent_id'])
        told = messages.agent(self.records, second['agent_id'])['rules']
        for text in (first['agent_id'], 'Plan the release notes', 'desk transcript', '"agent": "codex"'):
            self.assertIn(text, told)
        value = control.status(self.records, controller)
        self.assertEqual([item['provider'] for item in value['assistants']], ['claude-code', 'codex'], 'newest first')
        self.assertEqual((value['assistant']['provider'], value['assistant']['title']), ('claude-code', 'hello'))
        self.assertEqual(control.status(self.records, controller, 'codex')['assistant']['agent_id'], first['agent_id'])
        self.assertTrue(value['assistants'][1]['native_session_id'])
        # Back to the first agent: its own conversation continues, now told about the second.
        third = control.assistant(self.records, controller, 'continue', provider='codex', model='native-model', effort='high')
        self.assertEqual(third['agent_id'], first['agent_id'])
        engine.wait(self.records, [third['call_id']])
        self.assertEqual(third['requested_selection'], {'model': 'native-model', 'effort': 'high'})
        self.assertIn(second['agent_id'], messages.agent(self.records, first['agent_id'])['rules'])
        # Without an agent, the latest conversation continues.
        latest = control.assistant(self.records, controller, 'and then?')
        self.assertEqual(latest['agent_id'], first['agent_id'])
        engine.wait(self.records, [latest['call_id']])
        # The transcript an assistant reads: the user's messages and the replies, in order.
        seen = control.transcript(self.records, controller, first['agent_id'], limit=2)
        self.assertEqual((seen['title'], seen['provider'], seen['earlier_turns']), ('Plan the release notes', 'codex', 1))
        self.assertEqual([turn['user'] for turn in seen['turns']], ['continue', 'and then?'])
        self.assertTrue(all(turn['reply'] and turn['outcome'] == 'completed' for turn in seen['turns']))
        main = engine.start_call(self.records, 'codex', 'a main conversation', self.root, controller=controller)
        engine.wait(self.records, [main['call_id']])
        with self.assertRaises(SwarmError) as raised:
            control.transcript(self.records, controller, main['agent_id'])
        self.assertEqual(raised.exception.code, 'not_an_assistant_conversation')
        self.assertNotIn(second['agent_id'], messages.agent(self.records, first['agent_id'])['peers'], "assistants are not each other's peers")

    def test_peer_refresh_only_grants_this_desks_other_main_conversations(self):
        controller = control.initialize(self.records)['controller_id']
        first = control.assistant(self.records, controller, 'hello', workspace=str(self.root))
        engine.wait(self.records, [first['call_id']])
        own = engine.start_call(self.records, 'codex', 'owned main', self.root, controller=controller)
        foreign = engine.start_call(self.records, 'codex', 'foreign main', self.root)
        engine.wait(self.records, [own['call_id'], foreign['call_id']])
        second = control.assistant(self.records, controller, 'what is new')
        engine.wait(self.records, [second['call_id']])
        task = read_json(self.records.call(second['call_id'])/'task.json')
        self.assertEqual(task['communication']['peers'], [own['agent_id']])

    def test_inherited_call_identity_is_rejected(self):
        controller = control.initialize(self.records)['controller_id']
        with patch.dict(os.environ, {'AUTOSWARM_CALL_ID': 'call_'+'a'*32}):
            with self.assertRaises(SwarmError) as raised:
                control.assistant(self.records, controller, 'hello')
        self.assertEqual(raised.exception.code, 'desk_context_conflict')
        self.assertEqual(list(self.records.calls()), [])

    def test_nested_parser_accepts_prompt_after_flags(self):
        args = parse_args(['desk','assistant','--agent','codex','--cwd',str(self.root),'你好'])
        self.assertEqual(args.prompt, '你好')
        self.assertEqual(args.desk_command, 'assistant')
        args = parse_args(['desk','start','--agent','codex','--cwd',str(self.root),'新会话'])
        self.assertEqual((args.desk_command, args.prompt), ('start', '新会话'))

    def test_desk_start_creates_a_main_under_desk_that_leaves_decisions_to_the_user(self):
        code, value = self.cli('desk', 'init')
        controller = value['data']['controller_id']
        prompt = '整理测试报告 🐝\n保持原话'
        code, value = self.cli('--controller', controller, 'desk', 'start', '--agent', 'codex', '--cwd', str(self.root),
                               '--model', 'native-model', '--effort', 'high', '--input', '-', prompt=prompt)
        self.assertEqual(code, 0, value)
        main = value['data']
        self.assertEqual(main['controller_id'], controller)
        self.assertEqual(main['agent_id'], main['call_id'])
        self.assertEqual(main['requested_selection'], {'model': 'native-model', 'effort': 'high'})
        task = read_json(self.records.call(main['call_id']) / 'task.json')
        self.assertEqual(task['prompt'], prompt)
        self.assertFalse(task['delegate_approvals'])
        self.assertEqual(task['return_mode'], 'wait')
        self.assertIsNone(task['parent'])
        self.assertIn('started directly in AutoSwarm Desk', task['communication']['rules'])
        self.assertEqual(engine.wait(self.records, [main['call_id']])[0]['outcome'], 'completed')
        # The assistant may hand later input to it, like the mains it starts itself.
        self.assertEqual(control._peers(self.records, controller, None), [main['agent_id']])
        code, value = self.cli('--controller', controller, 'desk', 'start', '--agent', 'codex', '--cwd', str(self.root / 'missing'), '--input', '-', prompt='x')
        self.assertEqual((code, value['error']['code']), (1, 'workspace_not_found'))
        with patch.dict(os.environ, {'AUTOSWARM_CALL_ID': main['call_id']}):
            with self.assertRaises(SwarmError) as raised:
                control.start(self.records, controller, 'pretend user', provider='codex', workspace=str(self.root))
        self.assertEqual(raised.exception.code, 'desk_context_conflict')

    def test_cancel_stops_a_conversation_and_its_running_workers_only(self):
        controller = control.initialize(self.records)['controller_id']
        gate = self.root / 'never'
        main = control.start(self.records, controller, f'gate:{gate}\nmain work', provider='codex', workspace=str(self.root))
        done = engine.start_call(self.records, 'codex', 'finished worker', self.root, controller=main['agent_id'], return_mode='wait')
        engine.wait(self.records, [done['call_id']])
        worker = engine.start_call(self.records, 'codex', f'gate:{gate}\nworker', self.root, controller=main['agent_id'], return_mode='wait')
        nested = engine.start_call(self.records, 'codex', f'gate:{gate}\nnested', self.root, controller=worker['agent_id'], return_mode='wait')
        other = engine.start_call(self.records, 'codex', f'gate:{gate}\nunrelated', self.root, return_mode='wait')
        value = control.cancel(self.records, controller, worker['call_id'])
        self.assertEqual(value, {'agent_id': worker['agent_id'], 'cancel_requested': [worker['call_id'], nested['call_id']]})
        value = control.cancel(self.records, controller, main['agent_id'])
        self.assertEqual(value['cancel_requested'][0], main['call_id'], 'the owner is stopped before its workers')
        results = engine.wait(self.records, [main['call_id'], worker['call_id'], nested['call_id']])
        self.assertEqual({r['outcome'] for r in results}, {'cancelled'})
        self.assertEqual(engine.wait(self.records, [done['call_id']])[0]['outcome'], 'completed')
        self.assertEqual(engine.inspect_call(self.records, other['call_id'])['state'], 'running')
        with patch.dict(os.environ, {'AUTOSWARM_CALL_ID': other['call_id']}):
            with self.assertRaises(SwarmError) as raised:
                control.cancel(self.records, controller, other['agent_id'])
        self.assertEqual(raised.exception.code, 'desk_context_conflict')
        code, value = self.cli('--controller', controller, 'desk', 'cancel', other['agent_id'])
        self.assertEqual((code, value['data']['cancel_requested']), (0, [other['call_id']]))
