import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from autoswarm import engine, platform
from autoswarm.providers import invocation
from autoswarm.records import Records, SwarmError, read_json, write_json

if __package__:
    from .autoswarm_test_support import cleanup_temporary
else:
    from autoswarm_test_support import cleanup_temporary


FIXTURE = Path(__file__).parent / "fixtures/provider.py"


class ACPAuthenticateTests(unittest.TestCase):
    """ACP `authenticate` runs once, only with a configured method, then the session call is retried once."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.records = Records(self.root / "state")
        self.trace = self.root / "trace.jsonl"
        self.ids = []

    def tearDown(self):
        for call_id in self.ids:
            engine.cancel(self.records, call_id)
        deadline = time.monotonic() + 10  # Test harness deadline, not product policy.
        while any(platform.running(read_json(self.records.call(value) / "handle.json")) for value in self.ids) and time.monotonic() < deadline:
            time.sleep(0.01)
        cleanup_temporary(self.temporary, deadline)

    def run_call(self, flags, provider="antigravity", prompt="hello", **settings):
        write_json(self.records.home / "config.json", {"providers": {provider: {"command": [sys.executable, str(FIXTURE), provider], **settings}}})
        call = engine.start_call(self.records, provider, prompt, self.root, provider_args=[*flags, "--selection-trace=" + str(self.trace)])
        self.ids.append(call["call_id"])
        return engine.wait(self.records, [call["call_id"]])[0]

    def methods(self):
        lines = self.trace.read_text(encoding="utf-8").splitlines() if self.trace.exists() else []
        return [value.get("method") for value in map(json.loads, lines) if "method" in value]

    def test_authenticates_with_configured_method_then_retries_session(self):
        for provider in ("antigravity", "gemini", "cursor"):
            with self.subTest(provider=provider):
                self.trace.unlink(missing_ok=True)
                result = self.run_call(["auth-required", "auth-methods"], provider, auth_method="oauth-personal")
                self.assertEqual(result["outcome"], "completed", result)
                self.assertEqual(result["native_session_id"], "fixture-session")
                self.assertEqual(self.methods()[:4], ["initialize", "session/new", "authenticate", "session/new"])

    def test_resume_authenticates_before_loading_the_session(self):
        first = self.run_call(["auth-required", "auth-methods"], auth_method="oauth-personal")
        self.trace.unlink()
        continued = engine.resume(self.records, first["call_id"], "follow up")
        self.ids.append(continued["call_id"])
        result = engine.wait(self.records, [continued["call_id"]])[0]
        self.assertEqual(result["outcome"], "completed", result)
        self.assertEqual(self.methods()[:4], ["initialize", "session/load", "authenticate", "session/load"])

    def test_no_configured_method_names_advertised_methods_and_config_key(self):
        result = self.run_call(["auth-required", "auth-methods"])
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["error_code"], "native_auth_required")
        for text in ("providers.antigravity.auth_method", "oauth-personal", "api-key"):
            self.assertIn(text, result["error"])
        self.assertNotIn("authenticate", self.methods())

    def test_unadvertised_method_is_not_sent(self):
        result = self.run_call(["auth-required", "auth-methods"], auth_method="not-offered")
        self.assertEqual(result["error_code"], "native_auth_required")
        self.assertIn("'not-offered' is not advertised", result["error"])
        self.assertNotIn("authenticate", self.methods())

    def test_server_without_auth_methods_keeps_auth_required(self):
        result = self.run_call(["auth-required"], auth_method="oauth-personal")
        self.assertEqual(result["error_code"], "native_auth_required")
        self.assertNotIn("authenticate", self.methods())

    def test_authenticate_error_is_reported(self):
        result = self.run_call(["auth-required", "auth-methods", "auth-reject"], auth_method="oauth-personal")
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["error_code"], "native_auth_failed")
        self.assertIn("Sign-in was cancelled", result["error"])
        self.assertEqual(self.methods().count("session/new"), 1)

    def test_authenticate_has_its_own_timeout(self):
        result = self.run_call(["auth-required", "auth-methods", "auth-hang"], auth_method="oauth-personal", auth_timeout=0.5)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["error_code"], "native_auth_timeout")
        self.assertIn("did not finish within 0.5s", result["error"])

    def test_session_is_retried_once_without_a_sign_in_loop(self):
        result = self.run_call(["auth-required", "auth-methods", "auth-ineffective"], auth_method="oauth-personal")
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["error_code"], "native_auth_required")
        self.assertEqual(self.methods(), ["initialize", "session/new", "authenticate", "session/new"])

    def test_auth_settings_are_validated(self):
        task = {"provider": "antigravity"}
        invocation(task, {"providers": {"antigravity": {"command": [sys.executable]}}})
        self.assertEqual((task["auth_method"], task["auth_timeout"]), (None, 600))
        for settings in ({"auth_method": ""}, {"auth_method": 3}, {"auth_timeout": 0}, {"auth_timeout": "60"}, {"auth_timeout": True}):
            with self.subTest(settings=settings), self.assertRaises(SwarmError) as caught:
                invocation({"provider": "antigravity"}, {"providers": {"antigravity": {"command": [sys.executable], **settings}}})
            self.assertEqual(caught.exception.code, "invalid_config")


if __name__ == "__main__":
    unittest.main()
