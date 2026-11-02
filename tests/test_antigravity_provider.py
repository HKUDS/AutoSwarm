import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoswarm.providers import ACP_PROVIDERS, OWNED_SERVERS, canonical, invocation
from autoswarm.records import Records
from autoswarm.setup import install


class AntigravityProviderTest(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.TemporaryDirectory()
        path = Path(self.bin.name) / "agy_acp_server.par"
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.path = mock.patch.dict(os.environ, {"PATH": self.bin.name})
        self.path.start()

    def tearDown(self):
        self.path.stop()
        self.bin.cleanup()

    def test_aliases_resolve(self):
        for name in ("antigravity", "antigravity-acp", "google-antigravity"):
            self.assertEqual(canonical(name), "antigravity")

    def test_runs_over_acp(self):
        self.assertIn("antigravity", ACP_PROVIDERS)
        self.assertIn("antigravity", OWNED_SERVERS)

    def test_default_invocation_has_no_acp_flag(self):
        argv = invocation({"provider": "antigravity"}, {})
        self.assertEqual(argv[1:], [])
        self.assertTrue(argv[0].endswith("agy_acp_server.par"))

    def test_model_uses_acp_session_controls(self):
        task = {"provider": "antigravity", "model": "gemini-3.1-pro-high"}
        argv = invocation(task, {})
        self.assertEqual(argv[1:], [])
        self.assertEqual(task["native_options"]["model"], "gemini-3.1-pro-high")

    def test_configured_command_and_native_args(self):
        argv = invocation({"provider": "antigravity", "provider_args": ["--debug"]}, {"providers": {"antigravity": {"command": [str(Path(self.bin.name) / "agy_acp_server.par")]}}})
        self.assertEqual(argv[1:], ["--debug"])

    def test_setup_needs_explicit_skill_directory(self):
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaises(ValueError):
                install(Records(home), "antigravity")


if __name__ == "__main__":
    unittest.main()
