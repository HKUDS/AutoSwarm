"""Shared cleanup for subprocess fixtures, within each test's existing deadline."""
import errno
import time


def cleanup_temporary(temporary, deadline):
    while True:
        try:
            temporary.cleanup()
            return
        except OSError as error:
            # Windows launchers can hold runner.log briefly after their Python
            # workers exit. Python 3.10 may wrap the original sharing error.
            # A fixture that is still exiting can also add a file while the
            # folder is being removed (ENOTEMPTY).
            transient = error
            while transient and getattr(transient, "winerror", None) not in {32, 33} and getattr(transient, "errno", None) != errno.ENOTEMPTY:
                transient = transient.__context__
            if transient is None or time.monotonic() >= deadline:
                raise
            time.sleep(.01)
