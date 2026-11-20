import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoswarm.providers import ACP_PROVIDERS, OWNED_SERVERS, canonical, invocation


class GeminiProviderTest(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.TemporaryDirectory()
        path = Path(self.bin.name) / "gemini"
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.path = mock.patch.dict(os.environ, {"PATH": self.bin.name})
        self.path.start()

    def tearDown(self):
        self.path.stop()
        self.bin.cleanup()

    def test_aliases_resolve(self):
        for name in ("gemini", "gemini-cli", "google-gemini"):
            self.assertEqual(canonical(name), "gemini")

    def test_runs_over_acp(self):
        self.assertIn("gemini", ACP_PROVIDERS)
        self.assertIn("gemini", OWNED_SERVERS)

    def test_default_invocation(self):
        argv = invocation({"provider": "gemini"}, {})
        self.assertEqual(argv[1:], ["--acp"])
        self.assertTrue(argv[0].endswith("gemini"))

    def test_model_selection(self):
        argv = invocation({"provider": "gemini", "model": "gemini-3-pro"}, {})
        self.assertEqual(argv[1:], ["--model", "gemini-3-pro", "--acp"])

    def test_native_args_pass_through(self):
        argv = invocation({"provider": "gemini", "provider_args": ["--approval-mode", "auto_edit"]}, {})
        self.assertEqual(argv[1:], ["--approval-mode", "auto_edit", "--acp"])


if __name__ == "__main__":
    unittest.main()
