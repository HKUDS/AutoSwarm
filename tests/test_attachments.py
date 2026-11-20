"""Attachments: saved with the records, carried by the path a task or message names, images delivered natively."""
import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import attachments, engine, messages, platform
from autoswarm.desk import control
from autoswarm.records import Records, SwarmError, read_json, write_json
try:
    from autoswarm_test_support import cleanup_temporary
except ImportError:  # Run as tests.<module> from the checkout.
    from tests.autoswarm_test_support import cleanup_temporary

FIXTURE = Path(__file__).parent / "fixtures/provider.py"
ROOT = Path(__file__).resolve().parents[1]
CLEAN = {key: value for key, value in os.environ.items()
         if not key.startswith("AUTOSWARM_") and key not in {"CODEX_THREAD_ID", "CODEX_HOME"}}
IMAGE = b"\x89PNG fixture image"


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="attachments-中文 ")
        self.root = Path(self.temp.name).resolve()
        self.trace = self.root / "trace.jsonl"
        self.records = Records(self.root / "records")
        write_json(self.records.home / "config.json", {"providers": {
            provider: {"command": [sys.executable, str(FIXTURE), provider, "--selection-trace=" + str(self.trace)]}
            for provider in ["claude-code", "codex", "kimi", "dsh", "opencode"]}})
        self.patch = patch.dict(os.environ, dict(CLEAN, PYTHONPATH=str(ROOT / "src")), clear=True)
        self.patch.start()
        self.controller = control.initialize(self.records)["controller_id"]
        self.image = self.root / "screen shot.png"
        self.image.write_bytes(IMAGE)
        self.notes = self.root / "notes.txt"
        self.notes.write_text("notes", encoding="utf-8")

    def tearDown(self):
        calls = [task["call_id"] for _, task in self.records.calls()]
        for cid in calls:
            engine.cancel(self.records, cid)
        deadline = time.monotonic() + 10
        while any(platform.running(read_json(self.records.call(cid) / "handle.json")) for cid in calls) and time.monotonic() < deadline:
            time.sleep(.01)
        self.patch.stop()
        cleanup_temporary(self.temp, deadline)

    def traced(self):
        return [json.loads(line) for line in self.trace.read_text(encoding="utf-8").splitlines()] if self.trace.exists() else []

    def task(self, info):
        return read_json(self.records.call(info["call_id"]) / "task.json")

    def test_attachments_are_kept_in_the_records_and_found_by_their_path_in_any_form(self):
        stored = attachments.store(self.records, [self.image, self.notes])
        self.assertEqual([item["kind"] for item in stored], ["image", "file"])
        self.assertTrue(all(Path(item["path"]).parent.parent == self.records.home / "attachments" for item in stored))
        self.image.unlink()
        self.assertEqual(Path(stored[0]["path"]).read_bytes(), IMAGE, "The record keeps its own copy")
        path = stored[0]["path"]
        for text in [f"look at {path}", f'see "{path}" please', "posix " + path.replace("\\", "/"), json.dumps({"image": path})]:
            self.assertEqual([item["name"] for item in attachments.mentioned(self.records, text)], ["screen shot.png"], text)
        self.assertEqual(attachments.mentioned(self.records, "a file that is not attached: " + str(self.notes)), [])
        with self.assertRaises(SwarmError) as raised:
            attachments.store(self.records, [self.root / "missing.png"])
        self.assertEqual(raised.exception.code, "attachment_not_found")

    def test_each_agent_receives_images_its_own_way_and_every_attachment_by_path(self):
        def start(provider):
            before = len(self.traced())
            info = control.start(self.records, self.controller, "look", provider=provider, workspace=str(self.root),
                                 attachments=[self.image, self.notes])
            engine.wait(self.records, [info["call_id"]])
            return info, self.task(info), self.traced()[before:]
        info, task, trace = start("codex")
        picture, notes = task["attachments"]
        turn = next(value for value in trace if value.get("method") == "turn/start")["params"]["input"]
        self.assertIn(notes["path"], turn[0]["text"], "Every attachment is named in the text")
        self.assertEqual(turn[1], {"type": "localImage", "path": picture["path"]})
        info, task, trace = start("claude-code")
        content = next(value for value in trace if value.get("type") == "user")["message"]["content"]
        self.assertEqual((content[1]["type"], base64.b64decode(content[1]["source"]["data"])), ("image", IMAGE))
        self.assertEqual(task["command"][task["command"].index("--add-dir") + 1], str(Path(task["attachments"][0]["path"]).parent))
        info, task, trace = start("kimi")
        prompt = next(value for value in trace if value.get("method") == "session/prompt")["params"]["prompt"]
        self.assertEqual(prompt[1]["type"], "image", "An ACP agent advertising images gets the image")
        info, task, trace = start("dsh")
        prompt = next(value for value in trace if value.get("method") == "session/prompt")["params"]["prompt"]
        self.assertEqual(len(prompt), 1, "DSH does not advertise images: the text names the file")
        self.assertIn("screen shot.png", prompt[0]["text"])
        self.assertTrue(read_json(self.records.call(info["call_id"]) / "handle.json")["images_as_paths"])
        info, task, trace = start("opencode")
        parts = next(value for value in trace if value.get("method") == "POST" and value["path"].endswith("/message"))["params"]["parts"]
        self.assertTrue(parts[1]["url"].startswith("data:image/png;base64,"))

    def test_a_main_agent_hands_an_image_to_a_worker_by_naming_its_path(self):
        main = control.start(self.records, self.controller, "hold:" + str(self.root / "held"), provider="codex",
                             workspace=str(self.root), attachments=[self.image])
        path = self.task(main)["attachments"][0]["path"]
        before = len(self.traced())
        worker = engine.start_call(self.records, "claude-code", f"Describe the screenshot at {path}", self.root, controller=main["agent_id"])
        engine.wait(self.records, [worker["call_id"]])
        self.assertEqual([item["path"] for item in self.task(worker)["attachments"]], [path])
        content = next(value for value in self.traced()[before:] if value.get("type") == "user")["message"]["content"]
        self.assertEqual(base64.b64decode(content[1]["source"]["data"]), IMAGE)
        # A message naming it carries it too, and the worker continuing for that message receives the image.
        before = len(self.traced())
        sent = messages.send(self.records, worker["agent_id"], f"Also compare it with {path}", sender=main["agent_id"])
        self.assertEqual([item["path"] for item in sent["attachments"]], [path])
        deadline = time.monotonic() + 30
        while messages.agent(self.records, worker["agent_id"])["call_id"] == worker["call_id"] and time.monotonic() < deadline:
            time.sleep(.05)
        again = messages.agent(self.records, worker["agent_id"])["call_id"]
        engine.wait(self.records, [again])
        content = next(value for value in self.traced()[before:] if value.get("type") == "user")["message"]["content"]
        self.assertEqual(base64.b64decode(content[1]["source"]["data"]), IMAGE)
        self.assertIn("--add-dir", read_json(self.records.call(again) / "task.json")["command"])

    def test_desk_input_to_an_existing_conversation_carries_its_attachments(self):
        info = control.start(self.records, self.controller, "first", provider="kimi", workspace=str(self.root))
        engine.wait(self.records, [info["call_id"]])
        before = len(self.traced())
        sent = control.message(self.records, self.controller, info["agent_id"], "what is in this picture?", attachments=[self.image])
        self.assertEqual([item["name"] for item in sent["message"]["attachments"]], ["screen shot.png"])
        engine.wait(self.records, [messages.agent(self.records, info["agent_id"])["call_id"]])
        prompt = next(value for value in self.traced()[before:] if value.get("method") == "session/prompt")["params"]["prompt"]
        self.assertEqual(prompt[1]["type"], "image")
        self.assertIn("screen shot.png", prompt[0]["text"])


if __name__ == "__main__":
    unittest.main()
