"""Exercise selection through real runners and native protocol peers."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import engine, platform
from autoswarm.cli import parse_args
from autoswarm.providers import BINARIES, invocation
from autoswarm.native import Native, Output
from autoswarm.records import Records, SwarmError, read_json, write_json
from autoswarm.selection import configure_acp, cursor_parameters, native_environment, validate_opencode_effort

if __package__:
    from .autoswarm_test_support import cleanup_temporary
else:
    from autoswarm_test_support import cleanup_temporary

FIXTURE = Path(__file__).parent / "fixtures/provider.py"


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.environment = patch.dict(os.environ, {"CURSOR_CONFIG_DIR": str(self.root / "native-cursor")})
        self.environment.start()
        self.records = Records(self.root / "state")
        self.config = {"providers": {name: {"command": [sys.executable, str(FIXTURE), name], **({"python": [sys.executable, str(FIXTURE), name]} if name == "nanobot" else {})} for name in BINARIES}}
        write_json(self.records.home / "config.json", self.config)

    def tearDown(self):
        for _, task in self.records.calls():
            engine.cancel(self.records, task["call_id"])
        deadline = time.monotonic() + 10  # Test cleanup deadline only.
        while any(platform.running(read_json(path / "handle.json")) for path, _ in self.records.calls()):
            if time.monotonic() > deadline:
                self.fail("Fixture runner did not exit")
            time.sleep(.02)
        try:
            cleanup_temporary(self.tmp, deadline)
        finally:
            self.environment.stop()

    def run_child(self, provider, **kwargs):
        trace = self.root / (provider + ".jsonl")
        args = ["--selection-trace=" + str(trace), *kwargs.pop("provider_args", [])]
        call = engine.start_call(self.records, provider, "selection check", self.root, return_mode="wait", provider_args=args, **kwargs)
        result = engine.wait(self.records, [call["call_id"]])[0]
        return call, result, self.events(provider)

    def events(self, provider):
        return [json.loads(line) for line in (self.root / (provider + ".jsonl")).read_text(encoding="utf-8").splitlines()]

    def test_defaults_send_no_selection_controls(self):
        for provider in BINARIES:
            with self.subTest(provider=provider):
                call, result, events = self.run_child(provider)
                self.assertEqual(result["outcome"], "completed", result)
                self.assertEqual(call["requested_selection"], {})
                self.assertFalse(any(event.get("method", "").startswith("session/set") for event in events))
                self.assertFalse({"--model", "--effort", "--reasoning-effort"}.intersection(events[0]["argv"]))
                for event in events:
                    params = event.get("params", {})
                    self.assertNotIn("model", params)
                    self.assertNotIn("effort", params)
                    self.assertNotIn("variant", params)
                self.assertFalse(any(event.get("path") in {"/config", "/agent", "/provider"} for event in events))

    def test_all_native_mappings_and_resume_preserve_explicit_choice(self):
        for provider in BINARIES:
            with self.subTest(provider=provider):
                model = "fixture/native-model" if provider == "opencode" else "native-model"
                call, result, events = self.run_child(provider, model=model, effort="deep-native", provider_args=["selection-catalog"])
                self.assertEqual(result["outcome"], "completed", result)
                expected = {"model": model, "effort": "deep-native"}
                self.assertEqual(result["requested_selection"], expected)
                self.assertEqual(engine.inspect_call(self.records, call["call_id"])["requested_selection"], expected)
                argv = events[0]["argv"]
                if provider == "codex":
                    thread = next(event for event in events if event.get("method") == "thread/start")
                    turn = next(event for event in events if event.get("method") == "turn/start")
                    self.assertNotIn("effort", thread["params"])
                    self.assertEqual(turn["params"]["model"], model)
                    self.assertEqual(turn["params"]["effort"], "deep-native")
                elif provider == "opencode":
                    prompt = next(event for event in events if event.get("path", "").endswith("/message") and event["method"] == "POST")
                    self.assertEqual(prompt["params"]["model"], {"providerID": "fixture", "modelID": "native-model"})
                    self.assertEqual(prompt["params"]["variant"], "deep-native")
                elif provider == "claude-code":
                    self.assertEqual(argv[argv.index("--effort") + 1], "deep-native")
                elif provider == "muse":
                    methods = [event.get("method") for event in events]
                    self.assertLess(methods.index("session/setModel"), methods.index("session/setReasoningEffort"))
                    self.assertLess(methods.index("session/setReasoningEffort"), methods.index("turn/start"))
                    setting = next(event for event in events if event.get("method") == "session/setReasoningEffort")
                    self.assertEqual(setting["params"]["reasoningEffort"], "deep-native")
                elif provider == "grok":
                    self.assertEqual(argv[argv.index("--model") + 1], model)
                    self.assertEqual(argv[argv.index("--reasoning-effort") + 1], "deep-native")
                    self.assertFalse(any(event.get("method", "").startswith("session/set") for event in events))
                elif provider == "cursor":
                    init = next(event for event in events if event.get("method") == "initialize")
                    self.assertTrue(init["params"]["clientCapabilities"]["_meta"]["parameterizedModelPicker"])
                    setting = next(event for event in events if event.get("method") == "session/set_config_option" and event["params"]["configId"] == "reasoning")
                    self.assertEqual(setting["params"]["value"], "deep-native")
                else:
                    setting = next(event for event in events if event.get("method") == "session/set_config_option" and event["params"]["configId"] == "thought_level")
                    self.assertEqual(setting["params"]["value"], "deep-native")
                    if provider == "copilot":
                        self.assertEqual(argv[argv.index("--reasoning-effort") + 1], "deep-native")
                continued = engine.resume(self.records, call["call_id"], "follow up", return_mode="wait")
                resumed = engine.wait(self.records, [continued["call_id"]])[0]
                self.assertEqual(resumed["outcome"], "completed", resumed)
                self.assertEqual(resumed["requested_selection"], expected)
                self.assertEqual(resumed["native_session_id"], result["native_session_id"])
                override = engine.resume(self.records, continued["call_id"], "change effort", effort="light-native", return_mode="wait")
                changed = engine.wait(self.records, [override["call_id"]])[0]
                self.assertEqual(changed["outcome"], "completed", changed)
                self.assertEqual(changed["requested_selection"], {"model": model, "effort": "light-native"})
                self.assertEqual(read_json(self.records.call(call["call_id"]) / "result.json"), result)

    def test_effort_only_keeps_native_model(self):
        for provider in BINARIES:
            with self.subTest(provider=provider):
                _, result, events = self.run_child(provider, effort="deep-native", provider_args=["selection-catalog"])
                self.assertEqual(result["outcome"], "completed", result)
                self.assertNotIn("--model", events[0]["argv"])
                self.assertEqual(result["requested_selection"], {"effort": "deep-native"})
                if provider == "opencode":
                    prompt = next(event for event in events if event.get("method") == "POST" and event.get("path", "").endswith("/message"))
                    self.assertNotIn("model", prompt["params"])

    def test_copilot_byok_without_catalog_uses_startup_selection(self):
        _, result, events = self.run_child("copilot", model="byok-model", effort="native-effort")
        self.assertEqual(result["outcome"], "completed", result)
        self.assertFalse(any(event.get("method", "").startswith("session/set") for event in events))
        self.assertIn("byok-model", events[0]["argv"])
        self.assertIn("native-effort", events[0]["argv"])

    def test_model_only_leaves_effort_at_native_default(self):
        for provider in BINARIES:
            with self.subTest(provider=provider):
                model = "fixture/native-model" if provider == "opencode" else "native-model"
                _, result, events = self.run_child(provider, model=model, provider_args=["selection-catalog"])
                self.assertEqual(result["outcome"], "completed", result)
                self.assertEqual(result["requested_selection"], {"model": model})
                self.assertFalse({"--effort", "--reasoning-effort"}.intersection(events[0]["argv"]))
                for event in events:
                    self.assertNotIn("effort", event.get("params", {}))
                    self.assertNotIn("variant", event.get("params", {}))
                    self.assertNotEqual(event.get("method"), "session/setReasoningEffort")
                    if event.get("method") == "session/set_config_option":
                        self.assertEqual(event["params"]["configId"], "model")

    def test_unsupported_effort_fails_before_prompt_without_fallback(self):
        for provider in ("codebuddy", "opencode", "cursor"):
            with self.subTest(provider=provider):
                _, result, events = self.run_child(provider, effort="unsupported-native")
                self.assertEqual(result["outcome"], "failed", result)
                self.assertEqual(result["error_code"], "unsupported_effort")
                self.assertFalse(any(event.get("method") in {"session/prompt", "turn/start"} or (event.get("method") == "POST" and event.get("path", "").endswith("/message")) for event in events))

    def test_native_selection_rejection_is_retained_without_prompt(self):
        for provider in ("cursor", "codebuddy", "muse"):
            with self.subTest(provider=provider):
                _, result, events = self.run_child(provider, model="native-model", effort="deep-native", provider_args=["selection-catalog", "reject-selection"])
                self.assertEqual(result["outcome"], "failed", result)
                self.assertIn("selection was rejected", result["error"])
                self.assertFalse(any(event.get("method") in {"session/prompt", "turn/start"} for event in events))

    def test_explicit_flags_override_legacy_args_once(self):
        for provider in ("claude-code", "copilot"):
            with self.subTest(provider=provider):
                task = {"provider": provider, "workspace": str(self.root), "model": "chosen", "effort": "chosen-effort", "provider_args": ["--model=old", "--effort", "old-effort"]}
                argv = invocation(task, self.config)
                effort_flag = "--reasoning-effort" if provider == "copilot" else "--effort"
                self.assertEqual(argv.count("--model"), 1)
                self.assertEqual(argv.count(effort_flag), 1)
                self.assertIn("chosen", argv)
                self.assertIn("chosen-effort", argv)
                self.assertNotIn("old-effort", argv)

    def test_empty_selection_is_rejected_before_accepting_call(self):
        for key in ("model", "effort"):
            with self.assertRaises(SwarmError) as raised:
                engine.start_call(self.records, "codex", "test", self.root, **{key: " "})
            self.assertEqual(raised.exception.code, "invalid_selection")
        self.assertEqual(list(self.records.calls()), [])

    def test_conflicting_opencode_variant_is_explicit(self):
        with self.assertRaises(SwarmError) as raised:
            self.run_child("opencode", effort="deep-native", provider_args=["--variant", "other"])
        self.assertEqual(raised.exception.code, "conflicting_selection")

    def test_cursor_private_config_is_cleaned_after_cancellation(self):
        trace = self.root / "cursor.jsonl"
        marker = self.root / "native-started"
        call = engine.start_call(self.records, "cursor", "hold:" + str(marker), self.root, model="native-model", effort="deep-native", provider_args=["selection-catalog", "--selection-trace=" + str(trace)], return_mode="wait")
        end = time.monotonic() + 10  # Fixture readiness only.
        while not marker.exists():
            if time.monotonic() > end:
                self.fail("Native fixture did not start")
            time.sleep(.02)
        private = Path(self.events("cursor")[0]["config_dir"])
        self.assertTrue(private.exists())
        engine.cancel(self.records, call["call_id"])
        result = engine.wait(self.records, [call["call_id"]])[0]
        self.assertEqual(result["outcome"], "cancelled", result)
        self.assertFalse(private.exists())

    def test_codex_continuation_keeps_choice_on_every_internal_turn(self):
        task = {"provider": "codex", "call_id": "call_" + "a" * 32, "native_options": {"model": "native-model", "effort": "native-effort"}}
        native = Native(self.records, task, None, Output(session_id="native-session"), lambda _: None)
        requests = []
        native.request = lambda method, params, callback: requests.append((method, params))
        try:
            native.continue_turn("original assignment")
            native.continue_turn("reply to urgent question, then continue original work")
            self.assertEqual(len(requests), 2)
            for method, params in requests:
                self.assertEqual(method, "turn/start")
                self.assertEqual({key: params[key] for key in ("model", "effort")}, task["native_options"])
        finally:
            native.broker.close()

    def test_cli_run_and_resume_accept_native_values_without_common_enum(self):
        for command in (["run", "--agent", "codex"], ["resume", "call_" + "a" * 32]):
            parsed = parse_args([*command, "--model", "provider/arbitrary-model", "--effort", "provider-native-level", "hello"])
            self.assertEqual(parsed.model, "provider/arbitrary-model")
            self.assertEqual(parsed.effort, "provider-native-level")
            self.assertEqual(parsed.prompt, "hello")


class NativeSelectionHelpersTests(unittest.TestCase):
    def test_cursor_applies_parameters_then_unified_effort_with_fresh_controls(self):
        for key in ("effort", "reasoning_effort", "reasoning"):
            class Native:
                task = {"provider": "cursor", "native_options": {"model": "model[context=1m,fast=true]", "effort": "low"}}
                output = type("Output", (), {"session_id": "native"})()
                calls = []
                def request(self, method, params, callback):
                    self.calls.append((method, params))
                    callback({"configOptions": [{"id": "context", "options": [{"value": "1m"}]}, {"id": "fast", "options": [{"value": "true"}]}, {"id": key, "category": "thought_level", "options": [{"value": "low"}]}]})
            native = Native()
            finished = []
            configure_acp(native, {"configOptions": [{"id": "model"}]}, lambda: finished.append(True))
            self.assertEqual([(value["configId"], value["value"]) for _, value in native.calls], [("model", "model"), ("context", "1m"), ("fast", "true"), (key, "low")])
            self.assertEqual(finished, [True])

    def test_cursor_auto_does_not_silently_choose_another_model(self):
        class Native:
            task = {"provider": "cursor", "native_options": {"effort": "low"}}
            def request(self, *args):
                raise AssertionError("Must not choose another model")
        with self.assertRaises(SwarmError) as raised:
            configure_acp(Native(), {"models": {"currentModelId": "default", "availableModels": [{"modelId": "other"}]}}, lambda: None)
        self.assertEqual(raised.exception.code, "unsupported_effort")

    def test_cursor_parameter_parser_preserves_native_values(self):
        self.assertEqual(cursor_parameters("model[context=1m,fast=false]"), ("model", {"context": "1m", "fast": "false"}))
        for invalid in ("model[effort]", "model[effort=]", "model[bad[effort=high]]"):
            with self.assertRaises(SwarmError):
                cursor_parameters(invalid)

    def test_cursor_explicit_selection_isolates_preferences_and_cleans_private_copy(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "native"
            source.mkdir()
            original = b'{"model":"original","permissions":{"keep":true}}'
            (source / "cli-config.json").write_bytes(original)
            (source / "mcp.json").write_text('{"keep":"native MCP configuration"}')
            (source / "chats").mkdir()
            (source / "chats/keep-native-history").write_text("native-history")
            env = {"CURSOR_CONFIG_DIR": str(source), "CURSOR_DATA_DIR": "native-data"}
            task = {"provider": "cursor", "native_options": {"effort": "native-effort"}}
            with native_environment(task, env) as selected:
                private = Path(selected["CURSOR_CONFIG_DIR"])
                self.assertEqual(selected["CURSOR_DATA_DIR"], "native-data")
                self.assertEqual((private / "cli-config.json").read_bytes(), original)
                self.assertTrue((private / "mcp.json").exists())
                self.assertEqual((private / "chats/keep-native-history").read_text(), "native-history")
                (private / "acp-sessions/session-proof").write_text("native-session")
                (private / "cli-config.json").write_text('{"model":"changed"}')
                self.assertEqual((source / "cli-config.json").read_bytes(), original)
            self.assertFalse(private.exists())
            self.assertEqual((source / "chats/keep-native-history").read_text(), "native-history")
            self.assertEqual((source / "acp-sessions/session-proof").read_text(), "native-session")
            self.assertEqual(env["CURSOR_CONFIG_DIR"], str(source))
            with native_environment({"provider": "cursor"}, env) as selected:
                self.assertIs(selected, env)

    def test_opencode_resume_validates_last_native_model(self):
        class Native:
            task = {"native_options": {"effort": "custom"}, "native_session_id": "native-session"}
            output = type("Output", (), {"session_id": "native-session"})()
            def http(self, method, path):
                return {"/config": {"model": "wrong/model"}, "/agent": [], "/session/native-session/message": [{"info": {"role": "user", "model": {"providerID": "custom", "modelID": "model"}}}], "/provider": {"all": [{"id": "custom", "models": {"model": {"variants": {"custom": {}}}}}]}}[path]
        validate_opencode_effort(Native())


if __name__ == "__main__":
    unittest.main()
