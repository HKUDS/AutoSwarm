from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autoswarm import capture
from autoswarm.capture import Capture
from autoswarm.cli import dispatch, parse_args
from autoswarm.desk.lens import normalize
from autoswarm.records import Records, SwarmError, read_json, write_json


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_settings_default_validate_and_fall_back(self):
        self.assertEqual(capture.settings({}), {"capture": False, "max_call_mb": 64})
        self.assertEqual(capture.settings({"events": {"capture": "auto", "max_call_mb": -1}}), {"capture": "auto", "max_call_mb": 64})
        self.assertEqual(capture.settings({"events": {"capture": "yes", "max_call_mb": 0}}), {"capture": False, "max_call_mb": 0})

    def test_auto_keeps_a_copy_only_for_agents_desk_cannot_read(self):
        config = {"events": {"capture": "auto"}}
        self.assertEqual({p: capture.captures(config, p) for p in ("claude-code", "codex", "cursor", "opencode", "copilot")},
                         {"claude-code": False, "codex": False, "cursor": False, "opencode": True, "copilot": True})
        self.assertTrue(capture.captures({"events": {"capture": True}}, "codex"))
        self.assertFalse(capture.captures({}, "opencode"), "off unless asked")

    def test_capture_stops_at_the_call_limit_with_one_marker(self):
        directory = self.home / "calls" / "call_cap"
        directory.mkdir(parents=True)
        stream = Capture(directory, {"events": {"capture": True, "max_call_mb": 100 / capture.MB}}, "opencode")
        stream.write(b'{"a":1}\n')
        stream.write(b"y" * 60)  # A line without its newline yet.
        stream.write(b"z" * 60 + b"\n")  # Would cross 100 bytes.
        stream.write(b'{"late":1}\n')
        stream.close()
        lines = (directory / "events.jsonl").read_bytes().split(b"\n")
        self.assertEqual(lines[0], b'{"a":1}')
        self.assertEqual(lines[1], b"y" * 60, "the marker starts on its own line")
        marker = json.loads(lines[2])
        self.assertEqual((marker["autoswarm"], marker["limit_bytes"]), ("capture_truncated", 100))
        self.assertEqual(lines[3:], [b""], "nothing is written after the marker")
        notice = normalize("opencode", marker)[0]
        self.assertEqual(notice["kind"], "notice")
        self.assertIn("上限", notice["title"])
        unlimited = self.home / "calls" / "call_free"
        unlimited.mkdir()
        free = Capture(unlimited, {"events": {"capture": True, "max_call_mb": 0}}, "opencode")
        free.write(b"x" * 500 + b"\n")
        free.close()
        self.assertEqual((unlimited / "events.jsonl").stat().st_size, 501)
        skipped = self.home / "calls" / "call_native"
        skipped.mkdir()
        native = Capture(skipped, {"events": {"capture": "auto"}}, "codex")
        native.write(b"{}\n")
        native.close()
        self.assertEqual(list(skipped.iterdir()), [], "Desk reads Codex from its own records; the live stream is gone afterwards")

    def test_cli_persists_validated_settings(self):
        records = Records(self.home)
        write_json(self.home / "config.json", {"providers": {"codex": {"command": ["codex"]}}})
        result = dispatch(parse_args(["config", "--capture-events", "auto", "--event-max-call-mb", "16"]), records)
        self.assertEqual(result["events"], {"capture": "auto", "max_call_mb": 16})
        self.assertEqual(read_json(self.home / "config.json")["providers"], {"codex": {"command": ["codex"]}}, "unrelated fields are preserved")
        with self.assertRaises(SwarmError):
            dispatch(parse_args(["config", "--capture-events", "on", "--event-max-call-mb", "-3"]), records)
        self.assertEqual(read_json(self.home / "config.json")["events"]["capture"], "auto", "an invalid value writes nothing")


class LiveStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_agents_read_natively_keep_a_live_stream_only_while_running(self):
        config = {"events": {"capture": "auto"}}
        stream = Capture(self.root, config, "codex")
        stream.mark("prompt")
        stream.write(b'{"method":"item/agentMessage/delta"}\n')
        live = self.root / "live.jsonl"
        self.assertEqual(len(live.read_bytes().splitlines()), 2)
        self.assertFalse((self.root / "events.jsonl").exists())
        stream.close()
        self.assertFalse(live.exists(), "removed when the call ends; the native record is the history")
        kept = self.root / "kept"
        kept.mkdir()
        other = Capture(kept, config, "opencode")
        other.write(b"{}\n")
        other.close()
        self.assertTrue((kept / "events.jsonl").exists(), "agents without readable records keep their copy")
        off = self.root / "off"
        off.mkdir()
        Capture(off, {}, "codex").write(b"{}\n")
        self.assertEqual(list(off.iterdir()), [], "nothing at all when Desk is not in use")


if __name__ == "__main__":
    unittest.main()
