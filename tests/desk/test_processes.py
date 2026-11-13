from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoswarm.desk import processes


@unittest.skipUnless(os.name == "nt", "Desk's updater runs on Windows")
class RunningFromFolderTests(unittest.TestCase):
    """What Desk's installer would stop: processes started from its folder."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.folder, self.sibling = root / "app", root / "app2"
        for folder in (self.folder, self.sibling):
            folder.mkdir()
            shutil.copy(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe", folder / "worker.exe")
        self.children = []

    def start(self, folder: Path) -> subprocess.Popen:
        # cmd waits for input on its open stdin until it is stopped.
        child = subprocess.Popen([str(folder / "worker.exe")], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def tearDown(self):
        for child in self.children:
            child.kill()
            child.wait()
            child.stdin.close()
        self.temp.cleanup()

    def pids(self, **options) -> set[int]:
        return {item["pid"] for item in processes.running_from(str(self.folder), **options)}

    def test_finds_processes_from_the_folder_only(self):
        inside, beside = self.start(self.folder), self.start(self.sibling)
        found = self.pids()
        self.assertIn(inside.pid, found)
        self.assertNotIn(beside.pid, found)
        self.assertEqual(self.pids(except_name="WORKER.EXE"), set())

    def test_leaves_out_what_the_given_process_started(self):
        mine = self.start(self.folder)
        self.assertNotIn(mine.pid, self.pids(children_of=os.getpid()))

    def test_a_reused_parent_id_does_not_hide_an_older_process(self):
        mine = self.start(self.folder)
        real = processes.identity
        later = str(int(real(mine.pid)) + 1)  # As if this test's process had started after its child.
        with mock.patch.object(processes, "identity", side_effect=lambda pid: later if pid == os.getpid() else real(pid)):
            self.assertIn(mine.pid, self.pids(children_of=os.getpid()))

    def test_command_prints_json(self):
        child = self.start(self.folder)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            processes.main([str(self.folder)])
        self.assertEqual([item["pid"] for item in json.loads(output.getvalue())], [child.pid])


if __name__ == "__main__":
    unittest.main()
