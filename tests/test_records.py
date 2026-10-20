import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm.records import read_json, write_json


class AtomicRecordTests(unittest.TestCase):
    def test_windows_reader_conflict_retries_without_publishing_partial_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            write_json(path, {"content": "previous"})
            replace = os.replace
            attempts = []
            def conflict_once(source, destination):
                attempts.append(source)
                if len(attempts) == 1:
                    self.assertEqual(read_json(path), {"content": "previous"})
                    error = PermissionError("Windows sharing violation")
                    error.winerror = 32
                    raise error
                return replace(source, destination)
            with patch("autoswarm.records.os.replace", side_effect=conflict_once):
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
            with patch("autoswarm.records.os.replace", side_effect=error) as replace:
                with self.assertRaises(PermissionError):
                    write_json(path, {"content": "new"})
                self.assertEqual(replace.call_count, 1)
            self.assertEqual(read_json(path), {"content": "previous"})
            self.assertEqual(list(path.parent.iterdir()), [path])
