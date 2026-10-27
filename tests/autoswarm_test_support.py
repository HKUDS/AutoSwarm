"""Shared cleanup for subprocess fixtures, within each test's existing deadline."""
import time


def cleanup_temporary(temporary, deadline):
    while True:
        try:
            temporary.cleanup()
            return
        except OSError as error:
            # Windows launchers can hold runner.log briefly after their Python
            # workers exit. Python 3.10 may wrap the original sharing error.
            sharing = error
            while sharing and getattr(sharing, "winerror", None) not in {32, 33}:
                sharing = sharing.__context__
            if sharing is None or time.monotonic() >= deadline:
                raise
            time.sleep(.01)
