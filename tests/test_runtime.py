from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import engine, platform
from autoswarm.hooks import handle_hook
from autoswarm.providers import discover
from autoswarm.records import Records, SwarmError, read_json, write_json
from autoswarm.setup import install


FIXTURE = Path(__file__).parent / "fixtures/provider.py"


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.records = Records(self.root / "state")
        write_json(self.records.home / "config.json", {"providers": {name: {"command": [sys.executable, str(FIXTURE), name]} for name in ("codex", "claude-code", "opencode")}})
        self.ids = []

    def tearDown(self):
        for call_id in self.ids:
            engine.cancel(self.records, call_id)
        deadline = time.monotonic() + 10  # Test harness deadline, not product policy.
        # Result publication and runner exit are separate events. Wait for
        # the actual log owner before deleting a fixture directory on Windows.
        while any(platform.running(read_json(self.records.call(value) / "handle.json")) for value in self.ids) and time.monotonic() < deadline:
            time.sleep(0.01)
        while True:
            try:
                self.temporary.cleanup()
                break
            except OSError as error:
                # Python 3.10 tempfile can wrap a sharing violation in
                # NotADirectoryError while trying its permission recovery.
                sharing = error
                while sharing and getattr(sharing, "winerror", None) not in {32, 33}:
                    sharing = sharing.__context__
                if sharing is None or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def start(self, provider="codex", prompt="hello", **kwargs):
        call = engine.start_call(self.records, provider, prompt, self.root, **kwargs)
        self.ids.append(call["call_id"])
        return call["call_id"]

    def test_all_providers_preserve_unicode_and_native_session(self):
        for provider in ("codex", "claude-code", "opencode"):
            with self.subTest(provider=provider):
                call_id = self.start(provider, "第一行\n$(not executed) `literal` ' \" 🐝")
                result = engine.wait(self.records, [call_id])[0]
                self.assertEqual(result["outcome"], "completed")
                self.assertTrue(result["content"].startswith("第一行\n$(not executed)"))
                self.assertEqual(result["native_session_id"], "fixture-session")
                continued = engine.resume(self.records, call_id, "follow up")
                self.ids.append(continued["call_id"])
                value = engine.wait(self.records, [continued["call_id"]])[0]
                self.assertEqual(value["native_session_id"], result["native_session_id"])
                self.assertNotEqual(value["call_id"], call_id)
                self.assertEqual(read_json(self.records.call(call_id) / "result.json"), result)

    def test_parallel_wait_any_and_cancellation(self):
        held = self.start(prompt="hold:" + str(self.root / "started"))
        ready = self.start("opencode")
        values = engine.wait(self.records, [held, ready], any_result=True)
        self.assertEqual([value["call_id"] for value in values], [ready])
        self.assertTrue(engine.cancel(self.records, held)["cancel_requested"])
        self.assertEqual(engine.wait(self.records, [held])[0]["outcome"], "cancelled")

    def test_failure_is_retained_and_not_success(self):
        result = engine.wait(self.records, [self.start("claude-code", "fail")])[0]
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("fixture failure", result["error"])
        self.assertEqual(result["permission_denials"], [{"tool_name": "Bash"}])

    def test_missing_cli_is_reported_before_accepting_work_and_can_be_configured(self):
        config = self.records.config()
        command = config["providers"]["codex"]["command"]
        config["providers"]["codex"]["command"] = [str(self.root / "missing-codex")]
        write_json(self.records.home / "config.json", config)
        agent = next(value for value in discover(config) if value["provider"] == "codex")
        self.assertFalse(agent["available"])
        self.assertIn("official CLI", agent["error"])
        with self.assertRaises(SwarmError) as caught:
            self.start()
        self.assertEqual(caught.exception.code, "provider_unavailable")
        self.assertEqual(list(self.records.calls()), [])
        config["providers"]["codex"]["command"] = command
        write_json(self.records.home / "config.json", config)
        self.assertEqual(engine.wait(self.records, [self.start()])[0]["outcome"], "completed")

    def test_configured_cli_is_reused_when_not_on_path(self):
        with patch.dict(os.environ, {"PATH": ""}):
            agents = discover(self.records.config())
        self.assertTrue(all(agent["available"] for agent in agents))

    def test_cli_probe_failure_preserves_diagnostics(self):
        config = self.records.config()
        config["providers"]["codex"]["command"].append("version-fail")
        agent = next(value for value in discover(config) if value["provider"] == "codex")
        self.assertFalse(agent["available"])
        self.assertEqual(agent["error"], "fixture CLI cannot start")
        self.assertIn("command", agent)

    def test_opencode_return_after_tool_call_is_not_completion(self):
        result = engine.wait(self.records, [self.start("opencode", "incomplete")])[0]
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["finish_reason"], "tool-calls")
        self.assertIn("tool-calls", result["error"])
        self.assertEqual(result["native_session_id"], "fixture-session")

    def test_large_prompt_uses_stdin_not_os_argv(self):
        text = "非ASCII\n" * 100000
        result = engine.wait(self.records, [self.start(prompt=text)])[0]
        self.assertEqual(result["content"], text + "|session=fixture-session")

    def test_background_survives_cli_exit(self):
        env = engine.runtime_env(self.records)
        process = subprocess.run([*engine.runtime_argv(), "run", "--agent", "codex", "--background", "--no-parent", "--cwd", str(self.root), "background"], env=env, capture_output=True, text=True, check=True)
        call_id = json.loads(process.stdout)["data"]["call_id"]
        self.ids.append(call_id)
        self.assertEqual(engine.wait(self.records, [call_id])[0]["outcome"], "completed")

    def test_missing_runner_is_interrupted_not_restarted(self):
        call_id = "call_" + "a" * 32
        write_json(self.records.call(call_id) / "task.json", {"call_id": call_id, "provider": "codex", "workspace": str(self.root)})
        result = engine.wait(self.records, [call_id])[0]
        self.assertEqual(result["outcome"], "interrupted")

    def test_resume_can_explicitly_remove_original_parent(self):
        parent = {"host": "claude-code", "session_id": "parent-A", "mode": "hook"}
        call_id = self.start(parent=parent)
        engine.wait(self.records, [call_id])
        process = subprocess.run([*engine.runtime_argv(), "resume", call_id, "--no-parent", "follow-up"], env=engine.runtime_env(self.records), capture_output=True, text=True, check=True)
        result = json.loads(process.stdout)["data"]
        self.ids.append(result["call_id"])
        self.assertIsNone(engine.inspect_call(self.records, result["call_id"])["parent"])
        self.assertEqual(result["native_session_id"], "fixture-session")

    def test_same_native_session_rejects_overlapping_writers(self):
        first = self.start(prompt="hold:" + str(self.root / "started"), native_session_id="owned")
        while not (self.root / "started").exists():
            time.sleep(0.01)
        second = self.start(native_session_id="owned")
        result = engine.wait(self.records, [second])[0]
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["error_code"], "session_busy")
        self.assertEqual(engine.inspect_call(self.records, first)["state"], "running")

    def test_invalid_native_arguments_fail_before_background_acceptance(self):
        write_json(self.records.home / "config.json", {"providers": {"codex": {"command": [sys.executable, str(FIXTURE), "codex"], "args": "--wrong-shape"}}})
        with self.assertRaises(SwarmError):
            self.start()
        self.assertEqual(list(self.records.calls()), [])

    def test_host_watch_does_not_consume_a_result_before_native_delivery(self):
        parent = {"host": "opencode", "session_id": "parent-A", "mode": "hook", "workspace": str(self.root), "host_instance": "original-host"}
        call_id = self.start(parent=parent)
        engine.wait(self.records, [call_id], mark_received=False)
        self.assertFalse((self.records.call(call_id) / "received.json").exists())
        argv = [*engine.runtime_argv(), "calls", "--pending-return", "--host", "opencode", "--parent-workspace", str(self.root)]
        process = subprocess.run(argv, env=engine.runtime_env(self.records), capture_output=True, text=True, check=True)
        self.assertEqual([x["call_id"] for x in json.loads(process.stdout)["data"]], [call_id])
        other = subprocess.run([*argv, "--parent-instance", "another-cli"], env=engine.runtime_env(self.records), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(other.stdout)["data"], [])
        other = subprocess.run([*argv, "--parent-session", "another-session"], env=engine.runtime_env(self.records), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(other.stdout)["data"], [])
        engine.receive(self.records, [call_id])
        process = subprocess.run(argv, env=engine.runtime_env(self.records), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(process.stdout)["data"], [])

    def test_invalid_call_path_rejected(self):
        with self.assertRaises(SwarmError):
            self.records.require("../../outside")

    def test_claude_stop_returns_results_to_exact_session(self):
        parent = {"host": "claude-code", "session_id": "parent-A", "mode": "hook"}
        call_id = self.start(parent=parent)
        while not (self.records.call(call_id) / "result.json").exists():
            time.sleep(0.01)
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"session_id":"unrelated-parent"}')), contextlib.redirect_stdout(output):
            self.assertEqual(handle_hook(self.records, "stop"), 0)
        self.assertEqual(output.getvalue(), "")
        self.assertFalse((self.records.call(call_id) / "received.json").exists())
        with patch("sys.stdin", io.StringIO('{"session_id":"parent-A"}')), contextlib.redirect_stdout(output):
            self.assertEqual(handle_hook(self.records, "stop"), 0)
        self.assertEqual(json.loads(output.getvalue())["decision"], "block")
        self.assertIn(call_id, output.getvalue())
        self.assertTrue((self.records.call(call_id) / "received.json").exists())

    def test_claude_async_result_uses_native_rewake_exit(self):
        parent = {"host": "claude-code", "session_id": "parent-B", "mode": "hook"}
        call_id = self.start(parent=parent)
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"session_id":"parent-B"}')), contextlib.redirect_stderr(output):
            self.assertEqual(handle_hook(self.records, "post-tool"), 2)
        self.assertIn(call_id, output.getvalue())
        receipt = read_json(self.records.call(call_id) / "delivery.json")
        self.assertEqual(receipt["state"], "submitted")
        self.assertFalse(receipt["continued"])

    def test_setup_merges_settings_and_launcher_is_relocatable(self):
        settings = self.root / "claude/settings.json"
        write_json(settings, {"model": "keep-native", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "unrelated-hook"}]}]}})
        skill = self.root / "installed skill"
        for _ in range(2):
            result = install(self.records, "claude-code", str(skill), str(settings))
        value = read_json(settings)
        self.assertEqual(value["model"], "keep-native")
        self.assertEqual(len(value["hooks"]["Stop"]), 2)
        self.assertNotIn("timeout", str(value))
        env = dict(os.environ, PYTHONPATH="")
        output = subprocess.run([*result["launcher"], "run", "--agent", "codex", "--no-parent", "--cwd", str(self.root), "relocated"], env=env, capture_output=True, text=True, check=True)
        data = json.loads(output.stdout)["data"]
        self.ids.append(data["call_id"])
        self.assertEqual(data["content"], "relocated|session=fixture-session")
