import json
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import engine, messages
from autoswarm.records import Records, _open_record, _replace_record, read_json, write_json


class AtomicRecordTests(unittest.TestCase):
    def test_optional_history_scan_skips_unreadable_records_but_direct_access_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            records = Records(temporary)
            private = records.call("call_01")
            readable = records.call("call_02")
            write_json(private / "task.json", {"call_id": "call_01"})
            write_json(readable / "task.json", {"call_id": "call_02"})
            original = read_json
            def read_with_private_history(path, default=None):
                if path.parent == private:
                    raise PermissionError("Private history from another security context")
                return original(path, default)
            with patch("autoswarm.records.read_json", side_effect=read_with_private_history):
                self.assertEqual(list(records.calls(skip_unreadable=True)), [(readable, {"call_id": "call_02"})])
                with self.assertRaises(PermissionError):
                    list(records.calls())
                with self.assertRaises(PermissionError):
                    records.require("call_01")

    def test_receive_collects_completed_call_despite_unreadable_unrelated_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            records = Records(temporary)
            controller = "main_" + "a" * 32
            write_json(messages.agent_path(records, controller) / "agent.json", {"agent_id": controller})
            private = records.call("call_01")
            directory = records.call("call_02")
            write_json(private / "task.json", {"call_id": "call_01"})
            write_json(directory / "task.json", {"call_id": "call_02", "provider": "codex", "workspace": temporary})
            result = {"call_id": "call_02", "outcome": "completed", "content": "collected"}
            write_json(directory / "result.json", result)
            original = read_json
            def read_with_private_history(path, default=None):
                if path.parent == private:
                    raise PermissionError("Private history from another security context")
                return original(path, default)
            with patch("autoswarm.records.read_json", side_effect=read_with_private_history):
                self.assertEqual(engine.receive(records, ["call_02"], controller=controller), [result])
            self.assertTrue((directory / "received.json").exists())

    def test_open_reader_keeps_snapshot_without_blocking_atomic_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "结果🐝.json"
            write_json(path, {"content": "previous"})
            failures = []
            def publish():
                try:
                    write_json(path, {"content": "完整结果🐝"})
                except BaseException as error:
                    failures.append(error)
            writer = threading.Thread(target=publish, daemon=True)
            try:
                with _open_record(path) as snapshot:
                    writer.start()
                    writer.join(10)  # Test harness only.
                    self.assertFalse(writer.is_alive(), "A record reader blocked atomic publication")
                    self.assertEqual(json.load(snapshot), {"content": "previous"})
            finally:
                writer.join(10)
            self.assertEqual(failures, [])
            self.assertEqual(read_json(path), {"content": "完整结果🐝"})

    def test_read_retries_native_sharing_conflict(self):
        import io
        error = PermissionError("Windows sharing violation")
        error.winerror = 32
        with patch("autoswarm.records._open_record", side_effect=[error, io.StringIO('{"content":"完整结果🐝"}')]) as opener:
            self.assertEqual(read_json(Path("decision.json")), {"content": "完整结果🐝"})
            self.assertEqual(opener.call_count, 2)

    def test_read_does_not_retry_real_access_denial(self):
        error = PermissionError("Access denied")
        error.winerror = 5
        with patch("autoswarm.records._open_record", side_effect=error) as opener:
            with self.assertRaises(PermissionError):
                read_json(Path("decision.json"))
            self.assertEqual(opener.call_count, 1)

    def test_windows_reader_conflict_retries_without_publishing_partial_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            write_json(path, {"content": "previous"})
            attempts = []
            def conflict_once(source, destination):
                attempts.append(source)
                if len(attempts) == 1:
                    self.assertEqual(read_json(path), {"content": "previous"})
                    error = PermissionError("Windows sharing violation")
                    error.winerror = 32
                    raise error
                return _replace_record(source, destination)
            with patch("autoswarm.records._replace_record", side_effect=conflict_once):
                write_json(path, {"content": "完整结果🐝"})
            self.assertEqual(read_json(path), {"content": "完整结果🐝"})
            self.assertEqual(len(attempts), 2)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_real_access_denial_does_not_loop_or_replace_the_previous_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            write_json(path, {"content": "previous"})
            error = PermissionError("Access denied")
            error.winerror = 5
            with patch("autoswarm.records._replace_record", side_effect=error) as replace:
                with self.assertRaises(PermissionError):
                    write_json(path, {"content": "new"})
                self.assertEqual(replace.call_count, 1)
            self.assertEqual(read_json(path), {"content": "previous"})
            self.assertEqual(list(path.parent.iterdir()), [path])
