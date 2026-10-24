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
from autoswarm.hooks import handle_hook, submit
from autoswarm.providers import ACP_PROVIDERS, BINARIES, discover
from autoswarm.records import Records, SwarmError, read_json, write_json
from autoswarm.setup import install
from autoswarm.doctor import probe


FIXTURE = Path(__file__).parent / "fixtures/provider.py"


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.records = Records(self.root / "state")
        write_json(self.records.home / "config.json", {"providers": {name: {"command": [sys.executable, str(FIXTURE), name], **({"python": [sys.executable, str(FIXTURE), name]} if name == "nanobot" else {})} for name in BINARIES}})
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
        for provider in BINARIES:
            with self.subTest(provider=provider):
                call_id = self.start(provider, "第一行\n$(not executed) `literal` ' \" 🐝")
                result = engine.wait(self.records, [call_id])[0]
                self.assertEqual(result["outcome"], "completed")
                self.assertTrue(result["content"].startswith("第一行\n$(not executed)"))
                self.assertEqual(result["native_session_id"], "fixture-session")
                continued = engine.resume(self.records, call_id, "follow up")
                self.ids.append(continued["call_id"])
                value = engine.wait(self.records, [continued["call_id"]])[0]
                self.assertNotIn("OLD TRANSCRIPT", value["content"])
                self.assertEqual(value["native_session_id"], result["native_session_id"])
                self.assertNotEqual(value["call_id"], call_id)
                self.assertEqual(read_json(self.records.call(call_id) / "result.json"), result)

    def test_resumed_turn_reports_execution_before_returning(self):
        for provider in ("codex", "claude-code"):
            with self.subTest(provider=provider):
                first = engine.wait(self.records, [self.start(provider)])[0]
                marker = self.root / (provider + "-resumed")
                continued = engine.resume(self.records, first["call_id"], "hold:" + str(marker), return_mode="wait")
                call_id = continued["call_id"]
                self.ids.append(call_id)
                try:
                    deadline = time.monotonic() + 10  # Fixture-only wait, not a task deadline.
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(marker.exists(), engine.inspect_call(self.records, call_id))
                    info = engine.inspect_call(self.records, call_id)
                    self.assertEqual(info["state"], "running")
                    self.assertEqual(info["native_session_id"], first["native_session_id"])
                    self.assertEqual(info["handle"]["phase"], "execution")
                finally:
                    engine.cancel(self.records, call_id)
                    result = engine.wait(self.records, [call_id])[0]
                    self.assertEqual(result["outcome"], "cancelled")

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

    def test_new_transports_reject_incompatible_protocol_and_report_auth(self):
        for provider in sorted(ACP_PROVIDERS | {"muse"}):
            for flag, code in (("bad-protocol", "native_protocol_error"), ("auth-required", "native_auth_required")):
                with self.subTest(provider=provider, flag=flag):
                    result = engine.wait(self.records, [self.start(provider, provider_args=[flag])])[0]
                    self.assertEqual(result["outcome"], "failed")
                    self.assertEqual(result["error_code"], code)
                    self.assertIn(result["diagnostics"]["phase"], {"handshake", "session"})

    def test_acp_resume_checks_capability_before_loading(self):
        call_id = self.start("cursor", provider_args=["no-resume"])
        engine.wait(self.records, [call_id])
        continued = engine.resume(self.records, call_id, "follow up")
        self.ids.append(continued["call_id"])
        result = engine.wait(self.records, [continued["call_id"]])[0]
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["error_code"], "session_unavailable")

    def test_new_transports_answer_unsupported_server_requests(self):
        for provider in sorted(ACP_PROVIDERS | {"muse"}):
            with self.subTest(provider=provider):
                call_id = self.start(provider, provider_args=["unknown-interaction"])
                events = engine.wait(self.records, [call_id])
                self.assertTrue(any(item.get("kind") == "communication_issue" for item in events), events)
                while not any(item.get("outcome") for item in events):
                    events = engine.wait(self.records, [call_id])
                result = next(item for item in events if item.get("outcome"))
                self.assertEqual(result["outcome"], "completed", result)

    def test_new_transports_keep_native_failure_and_allow_cancellation(self):
        for provider in sorted(ACP_PROVIDERS | {"muse"}):
            with self.subTest(provider=provider):
                result = engine.wait(self.records, [self.start(provider, "fail")])[0]
                self.assertEqual(result["outcome"], "failed")
                self.assertIn(result["finish_reason"], {"refusal", "failed"})
                held = self.start(provider, "hold:" + str(self.root / (provider + "-held")))
                engine.cancel(self.records, held)
                self.assertEqual(engine.wait(self.records, [held])[0]["outcome"], "cancelled")

    def test_acp_final_content_excludes_commentary_before_tools(self):
        for provider in sorted(ACP_PROVIDERS):
            with self.subTest(provider=provider):
                result = engine.wait(self.records, [self.start(provider, "with-tools")])[0]
                self.assertEqual(result["content"], "with-tools|session=fixture-session")

    def test_new_hosts_install_one_skill_and_use_explicit_collection(self):
        for host in sorted(ACP_PROVIDERS | {"muse"}):
            with self.subTest(host=host):
                directory = self.root / host
                result = install(self.records, host, str(directory))
                self.assertEqual(result["native_return"], "wait")
                self.assertEqual(list(directory.rglob("SKILL.md")), [directory / "SKILL.md"])
                env = engine.runtime_env(self.records)
                process = subprocess.run([*result["launcher"], "run", "--agent", host, "--no-parent", "--cwd", str(self.root), "installed child"], env=env, capture_output=True, text=True, check=True)
                value = json.loads(process.stdout)["data"]
                self.ids.append(value["call_id"])
                self.assertEqual(value["outcome"], "completed", value)

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

    def test_opencode_handshake_rejects_bad_health_or_incompatible_api(self):
        for flag in ("bad-health", "bad-protocol", "exit-startup"):
            with self.subTest(flag=flag):
                result = engine.wait(self.records, [self.start("opencode", provider_args=[flag])])[0]
                self.assertEqual(result["outcome"], "failed")
                self.assertIn(result["error_code"], {"native_protocol_error", "native_start_failed"})
                self.assertIn(result["diagnostics"]["phase"], {"startup", "handshake"})
                self.assertTrue(Path(result["diagnostics"]["directory"]).is_dir())

    def test_opencode_startup_progress_is_inspectable_and_cancellable(self):
        call_id = self.start("opencode", provider_args=["hold-startup"])
        self.assertEqual(engine.inspect_call(self.records, call_id)["handle"]["phase"], "startup")
        engine.cancel(self.records, call_id)
        self.assertEqual(engine.wait(self.records, [call_id])[0]["outcome"], "cancelled")

    def test_opencode_cold_health_request_can_yield_and_retry(self):
        call_id = self.start("opencode", provider_args=["slow-health"])
        deadline = time.monotonic() + 10  # Test harness only.
        while not (self.records.call(call_id) / "result.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(read_json(self.records.call(call_id) / "result.json", {}).get("outcome"), "completed")

    def test_local_server_auth_failure_is_not_a_model_login_error(self):
        for flag, path in (("reject-auth", "/global/health"), ("reject-events", "/event")):
            with self.subTest(flag=flag):
                result = engine.wait(self.records, [self.start("opencode", provider_args=[flag])])[0]
                self.assertEqual(result["error_code"], "native_server_auth_failed")
                self.assertIn(path, result["error"])

    def test_loopback_bypasses_proxy_and_uses_native_server_auth(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "", "no_proxy": "", "OPENCODE_SERVER_PASSWORD": "fixture-secret", "OPENCODE_SERVER_USERNAME": "fixture-user"}):
            result = engine.wait(self.records, [self.start("opencode", provider_args=["auth-required"])])[0]
        self.assertEqual(result["outcome"], "completed")

    def test_doctor_checks_real_transport_and_resume_in_existing_workspace(self):
        for provider in BINARIES:
            with self.subTest(provider=provider):
                report = probe(self.records, provider, str(self.root))
                self.ids.extend(report[key] for key in ("initial_call_id", "resume_call_id") if key in report)
                self.assertTrue(report["verified"], report)
                self.assertEqual(engine.inspect_call(self.records, report["initial_call_id"])["workspace"], str(self.root))
                self.assertEqual(report["initial"]["native_session_id"], report["resumed"]["native_session_id"])

    def test_cli_background_collects_without_a_wakeup_interface(self):
        env = engine.runtime_env(self.records)
        env["AUTOSWARM_PARENT"] = json.dumps({"host": "codex", "session_id": "parent-no-endpoint"})
        argv = [*engine.runtime_argv(), "run", "--agent", "codex", "--background", "--cwd", str(self.root), "parallel"]
        process = subprocess.run(argv, env=env, capture_output=True, text=True, check=True)
        info = json.loads(process.stdout)["data"]
        self.ids.append(info["call_id"])
        self.assertEqual(info["return_mode"], "wait")
        self.assertEqual(engine.wait(self.records, [info["call_id"]])[0]["outcome"], "completed")
        process = subprocess.run([*argv, "--notify-parent"], env=env, capture_output=True, text=True)
        self.assertEqual(json.loads(process.stdout)["error"]["code"], "parent_endpoint_required")

    def test_cli_doctor_distinguishes_discovery_from_verification(self):
        argv = [*engine.runtime_argv(), "doctor", "--agent", "opencode", "--cwd", str(self.root)]
        initial = subprocess.run(argv, env=engine.runtime_env(self.records), capture_output=True, text=True, check=True)
        discovery = json.loads(initial.stdout)["data"]
        self.assertEqual(len(discovery["agents"]), 1)
        self.assertEqual(discovery["agents"][0]["check"], "executable")
        self.assertFalse(discovery["agents"][0]["invocation_verified"])
        checked = subprocess.run([*argv, "--probe"], env=engine.runtime_env(self.records), capture_output=True, text=True, check=True)
        report = json.loads(checked.stdout)["data"]
        for result in report["probes"]:
            self.ids.extend(result[key] for key in ("initial_call_id", "resume_call_id") if key in result)
        self.assertTrue(report["verified"], report)
        self.assertTrue(report["agents"][0]["invocation_verified"])

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

    def test_live_runner_remains_available_without_pid_visibility(self):
        marker = self.root / "hidden-runner-started"
        call_id = self.start(prompt="hold:" + str(marker))
        while not marker.exists():
            time.sleep(0.01)
        with patch("autoswarm.platform.identity", return_value=None):
            self.assertEqual(engine.inspect_call(self.records, call_id)["state"], "running")
            self.assertEqual(engine.receive(self.records, [call_id]), [])
            with self.assertRaises(SwarmError) as caught:
                engine.resume(self.records, call_id, "must not duplicate running work")
            self.assertEqual(caught.exception.code, "session_busy")
            self.assertTrue(engine.cancel(self.records, call_id)["cancel_requested"])
        self.assertEqual(engine.wait(self.records, [call_id])[0]["outcome"], "cancelled")

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

    def test_hook_submission_shares_runner_delivery_lock(self):
        directory = self.root / "delivery-race"
        with platform.session_lease(directory / "delivery.lock"):
            self.assertFalse(submit(directory, {"state": "submitted"}))
            write_json(directory / "delivery.json", {"state": "pending"})
        self.assertTrue(submit(directory, {"state": "submitted"}))
        self.assertEqual(read_json(directory / "delivery.json")["state"], "submitted")

    def test_setup_merges_settings_and_launcher_is_relocatable(self):
        settings = self.root / "claude/settings.json"
        write_json(settings, {"model": "keep-native", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "unrelated-hook"}]}]}})
        skill = self.root / "installed skill"
        obsolete = skill / "scripts/runtime/autoswarm/assets/SKILL.md"
        obsolete.parent.mkdir(parents=True)
        obsolete.write_text("old nested template", encoding="utf-8")
        for _ in range(2):
            result = install(self.records, "claude-code", str(skill), str(settings))
        value = read_json(settings)
        self.assertEqual(value["model"], "keep-native")
        self.assertEqual(len(value["hooks"]["Stop"]), 2)
        self.assertNotIn("timeout", str(value))
        self.assertEqual(list(skill.rglob("SKILL.md")), [skill / "SKILL.md"])
        self.assertTrue((skill / "references/setup.md").is_file())
        env = dict(os.environ, PYTHONPATH="")
        reinstalled = subprocess.run([*result["launcher"], "setup", "--host", "codex", "--skill-dir", str(skill)], env=env, capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(reinstalled.stdout)["ok"])
        self.assertEqual(list(skill.rglob("SKILL.md")), [skill / "SKILL.md"])
        output = subprocess.run([*result["launcher"], "run", "--agent", "codex", "--no-parent", "--cwd", str(self.root), "relocated"], env=env, capture_output=True, text=True, check=True)
        data = json.loads(output.stdout)["data"]
        self.ids.append(data["call_id"])
        self.assertEqual(data["content"], "relocated|session=fixture-session")
