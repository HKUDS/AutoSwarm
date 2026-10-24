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
            process = subprocess.Popen([sys.executable, "-c", code, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                handle = {"pid": process.pid, "identity": "hidden", "runner_lock": str(lock)}
                with patch("autoswarm.platform.identity", return_value=None):
                    self.assertTrue(platform.running(handle))
                    process.terminate()
                    process.wait()
                    self.assertFalse(platform.running(handle))
                self.assertTrue(lock.exists())  # A stale file alone is not liveness.
            finally:
                if process.poll() is None:
                    process.terminate()
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


if __name__ == "__main__":
    unittest.main()
