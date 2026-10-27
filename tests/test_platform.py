from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import platform


class DirectoryLinkTests(unittest.TestCase):
    def test_link_reuses_native_storage_and_unlink_preserves_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "native space & % !"
            native.mkdir()
            link = root / "link space & % !"
            platform.link_directory(link, native)
            try:
                (link / "session.txt").write_text("native session")
                self.assertEqual((native / "session.txt").read_text(), "native session")
            finally:
                platform.unlink_directory(link)
            self.assertFalse(link.exists())
            self.assertEqual((native / "session.txt").read_text(), "native session")


class RunnerLeaseTests(unittest.TestCase):
    def test_shared_lease_survives_hidden_pid_and_releases_on_process_death(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "runner.lock"
            code = (
                "from pathlib import Path\n"
                "import sys\n"
                "from autoswarm.platform import session_lease\n"
                "with session_lease(Path(sys.argv[1])):\n"
                " print('locked', flush=True)\n"
                " sys.stdin.read()\n"
            )
            process = subprocess.Popen([sys.executable, "-c", code, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **platform.detached_options())
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                handle = {"pid": process.pid, "identity": "hidden", "runner_lock": str(lock)}
                with patch("autoswarm.platform.identity", return_value=None):
                    self.assertTrue(platform.running(handle))
                    platform.stop_child(process)
                    process.wait()
                    self.assertFalse(platform.running(handle))
                self.assertTrue(lock.exists())  # A stale file alone is not liveness.
            finally:
                if process.poll() is None:
                    platform.stop_child(process)
                process.communicate()

    def test_legacy_handles_keep_process_identity_protection(self):
        handle = {"pid": 123, "identity": "original"}
        with patch("autoswarm.platform.identity", return_value="original"):
            self.assertTrue(platform.running(handle))
        with patch("autoswarm.platform.identity", return_value="reused"):
            self.assertFalse(platform.running(handle))

    def test_unavailable_lease_is_not_reported_as_lock_contention(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = "msvcrt.locking" if os.name == "nt" else "fcntl.flock"
            with patch(target, side_effect=OSError(errno.EIO, "unavailable")):
                with self.assertRaises(OSError):
                    platform.session_lease(Path(temporary) / "runner.lock")


@unittest.skipUnless(os.name == "nt", "Windows process tree contracts")
class WindowsProcessTests(unittest.TestCase):
    def tree(self, exit_parent=False):
        # A Windows venv's python.exe redirects through another process. Use
        # the real interpreter so the fixture controls the parent it prints.
        python = getattr(sys, "_base_executable", sys.executable)
        code = ("import subprocess, sys\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
                "print(child.pid, flush=True)\n" + ("" if exit_parent else "sys.stdin.read()\n"))
        process = subprocess.Popen([python, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.close_tree, process)
        child = int(process.stdout.readline())
        return process, child

    def close_tree(self, process):
        platform.stop_child(process)
        process.communicate(timeout=5)

    def test_stops_descendants_without_launching_taskkill(self):
        process, child = self.tree()
        with patch("autoswarm.platform.subprocess.run", side_effect=AssertionError("taskkill must not be used")):
            platform.stop_child(process)
        self.assertIsNotNone(process.poll())
        self.assertIsNone(platform.identity(child))
        process.communicate(timeout=5)  # Descendants must release inherited pipes too.

    def test_exited_parent_does_not_leave_its_live_child(self):
        process, child = self.tree(exit_parent=True)
        process.wait(timeout=5)
        platform.stop_child(process)
        self.assertIsNone(platform.identity(child))

    def test_rescans_for_children_missing_from_the_first_snapshot(self):
        from autoswarm import _winprocess
        process, child = self.tree()
        original = _winprocess._snapshot
        calls = []
        def snapshot(*args):
            cutoff, rows = original(*args)
            calls.append(True)
            return cutoff, [] if len(calls) == 1 else rows
        with patch.object(_winprocess, "_snapshot", side_effect=snapshot):
            platform.stop_child(process)
        self.assertGreaterEqual(len(calls), 3)
        self.assertIsNone(platform.identity(child))

    def test_stops_multiple_generations_in_reversed_snapshot_order(self):
        from autoswarm import _winprocess
        branch_code = ("import subprocess, sys\n"
                       "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
                       "print(child.pid, flush=True)\nsys.stdin.read()\n")
        code = ("import subprocess, sys\n"
                f"child = subprocess.Popen([sys.executable, '-c', {branch_code!r}], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)\n"
                "print(child.pid, child.stdout.readline().strip(), flush=True)\nsys.stdin.read()\n")
        python = getattr(sys, "_base_executable", sys.executable)
        process = subprocess.Popen([python, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.close_tree, process)
        children = [int(value) for value in process.stdout.readline().split()]
        self.assertEqual(len(children), 2)
        original = _winprocess._snapshot
        def snapshot(*args):
            cutoff, rows = original(*args)
            return cutoff, list(reversed(rows))
        with patch.object(_winprocess, "_snapshot", side_effect=snapshot):
            platform.stop_child(process)
        for child in children:
            self.assertIsNone(platform.identity(child))
        process.communicate(timeout=5)

    def test_old_process_with_reused_parent_pid_is_not_terminated(self):
        from autoswarm import _winprocess
        sibling, _ = self.tree()
        process, child = self.tree()
        original = _winprocess._snapshot
        def snapshot(*args):
            cutoff, rows = original(*args)
            return cutoff, [(pid, process.pid if pid == sibling.pid else parent) for pid, parent in rows]
        with patch.object(_winprocess, "_snapshot", side_effect=snapshot):
            platform.stop_child(process)
        self.assertIsNone(sibling.poll())
        self.assertIsNone(platform.identity(child))

    def test_pid_reused_after_snapshot_is_not_terminated(self):
        from autoswarm import _winprocess
        process, _ = self.tree()
        kernel, _ = _winprocess._api()
        cutoff_before_reuse = _winprocess._created(kernel, int(process._handle))
        sibling, _ = self.tree()
        original = _winprocess._snapshot
        calls = []
        def snapshot(*args):
            cutoff, rows = original(*args)
            calls.append(True)
            if len(calls) == 1:
                return cutoff_before_reuse, [(sibling.pid, process.pid)]
            return cutoff, rows
        with patch.object(_winprocess, "_snapshot", side_effect=snapshot):
            platform.stop_child(process)
        self.assertIsNone(sibling.poll())

    def test_snapshot_failures_remain_errors(self):
        from autoswarm import _winprocess
        process, _ = self.tree()
        with patch.object(_winprocess, "_snapshot", side_effect=PermissionError("snapshot denied")):
            with self.assertRaisesRegex(PermissionError, "snapshot denied"):
                platform.stop_child(process)
        self.assertIsNone(process.poll())

    def test_concurrent_exit_access_denial_waits_for_completion(self):
        import ctypes
        from autoswarm import _winprocess
        process, child = self.tree()
        kernel, _ = _winprocess._api()
        terminate = kernel.TerminateProcess
        def concurrent_exit(handle, code):
            terminate(handle, code)
            ctypes.set_last_error(5)
            return 0
        with patch.object(kernel, "TerminateProcess", side_effect=concurrent_exit) as raced:
            platform.stop_child(process)
        self.assertTrue(raced.called)
        self.assertIsNotNone(process.poll())
        self.assertIsNone(platform.identity(child))

    def test_other_termination_failures_remain_errors(self):
        import ctypes
        from autoswarm import _winprocess
        process, _ = self.tree()
        kernel, _ = _winprocess._api()
        def rejected(handle, code):
            ctypes.set_last_error(6)  # ERROR_INVALID_HANDLE
            return 0
        with patch.object(kernel, "TerminateProcess", side_effect=rejected):
            with self.assertRaises(OSError) as error:
                platform.stop_child(process)
        self.assertEqual(error.exception.winerror, 6)
        self.assertIsNone(process.poll())


if __name__ == "__main__":
    unittest.main()
