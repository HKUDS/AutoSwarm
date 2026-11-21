import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm.cli import dispatch, parser
from autoswarm.providers import command_for, discover, enabled, invocation
from autoswarm.records import Records, SwarmError, read_json, write_json


class ProviderSwitchTests(unittest.TestCase):
    def test_disabled_provider_is_reported_but_cannot_be_invoked(self):
        config = {"providers": {"opencode": {"command": [sys.executable], "enabled": False}}}
        self.assertFalse(enabled("opencode", config))
        with self.assertRaises(SwarmError) as raised:
            invocation({"provider": "opencode"}, config)
        self.assertEqual(raised.exception.code, "provider_disabled")

        with patch("autoswarm.providers.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "fixture version"
            run.return_value.stderr = ""
            agent = discover(config, ["opencode"])[0]
        self.assertTrue(agent["available"])
        self.assertTrue(agent["configured"])
        self.assertFalse(agent["enabled"])

    def test_switch_defaults_on_and_preserves_other_provider_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            records = Records(Path(temporary) / "state")
            before = {
                "approvals": {"delegate_to_parent": False},
                "events": {"capture": "auto", "max_call_mb": 12},
                "providers": {
                    "opencode": {"command": [sys.executable], "model": "local-model"},
                    "codex": {"command": ["codex"], "keep": "as-is"},
                },
            }
            write_json(records.home / "config.json", before)
            self.assertTrue(command_for("opencode", before))

            args = parser().parse_args(["--home", str(records.home), "config", "--agent", "opencode", "--enabled", "off"])
            result = dispatch(args, records)
            saved = read_json(records.home / "config.json")

            self.assertEqual(result["agent"], {"provider": "opencode", "enabled": False})
            self.assertEqual(saved["providers"]["opencode"], {"command": [sys.executable], "model": "local-model", "enabled": False})
            self.assertEqual(saved["providers"]["codex"], before["providers"]["codex"])
            self.assertEqual(saved["approvals"], before["approvals"])
            self.assertEqual(saved["events"], before["events"])

            with self.assertRaises(SwarmError) as raised:
                command_for("opencode", saved)
            self.assertEqual(raised.exception.code, "provider_disabled")

    def test_mismatched_switch_options_fail_before_other_config_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            records = Records(Path(temporary) / "state")
            before = {"events": {"capture": False}, "providers": {"codex": {"keep": True}}}
            path = records.home / "config.json"
            write_json(path, before)
            original = path.read_bytes()

            args = parser().parse_args(["--home", str(records.home), "config", "--capture-events", "on", "--agent", "codex"])
            with self.assertRaises(SwarmError) as raised:
                dispatch(args, records)

            self.assertEqual(raised.exception.code, "invalid_config")
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
