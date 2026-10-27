"""Run the same package checks locally and in CI; optionally install a pre-push hook."""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK_MARKER = "# AutoSwarm pre-push: scripts/check.py"


class CheckError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def git(root, *args, check=True):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode:
        raise CheckError(result.stderr.strip() or "Git command failed", result.returncode)
    return result


def validate_push(root, updates):
    """Only validate pushes of the checked-out commit, with no unpublished edits."""
    commits = set()
    for line in updates.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", fields[1]):
            raise CheckError("Invalid pre-push input; no checks were run")
        if set(fields[1]) != {"0"}:
            commits.add(fields[1])
    if not commits:
        return False  # Deletion-only pushes have no code to validate.
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    for commit in commits:
        if git(root, "rev-parse", "--verify", commit + "^{commit}").stdout.strip() != head:
            raise CheckError("Push blocked: check out the commit being pushed so the checks test that exact code")
    if git(root, "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise CheckError("Push blocked: commit or stash outstanding changes first. Run scripts/check.py without --pre-push to check work in progress")
    return True


def install_hook(root):
    configured = git(root, "config", "--get", "core.hooksPath", check=False)
    if configured.returncode == 0:
        raise CheckError("core.hooksPath is already configured; integrate scripts/check.py --pre-push into that hook instead of replacing it")
    if configured.returncode != 1:
        raise CheckError(configured.stderr.strip() or "Could not inspect the Git hooks configuration")
    path = Path(git(root, "rev-parse", "--git-path", "hooks/pre-push").stdout.strip())
    if not path.is_absolute():
        path = root / path
    if path.is_symlink() or (path.exists() and HOOK_MARKER not in path.read_text(encoding="utf-8")):
        raise CheckError("An existing pre-push hook was left intact; integrate scripts/check.py --pre-push into it")
    # Git for Windows executes this with its own sh. Keep the selected Python
    # executable, since python on PATH can be the nonfunctional Store alias.
    python = shlex.quote(Path(sys.executable).as_posix())
    source = ("#!/bin/sh\n" + HOOK_MARKER + "\n"
              'root=$(git rev-parse --show-toplevel) || exit 1\n'
              f'exec {python} "$root/scripts/check.py" --pre-push\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(source)
    path.chmod(0o755)
    print(f"Installed {path}\nPython: {sys.executable}\nEach push will run the local CI checks before uploading.", flush=True)


def run_step(label, argv, root, env):
    print(f"\n==> {label}", flush=True)
    started = time.monotonic()
    result = subprocess.run([str(value) for value in argv], cwd=root, env=env, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise CheckError(f"{label} failed (exit {result.returncode}); push is blocked when running as a hook", result.returncode)
    print(f"Passed: {label} ({time.monotonic() - started:.1f}s)", flush=True)


def run_checks(root):
    node = shutil.which("node")
    if not node:
        raise CheckError("Node.js is required for the OpenCode plugin checks (CI uses Node 20)")
    work = Path(tempfile.mkdtemp(prefix="autoswarm-check-"))
    try:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("AUTOSWARM_") and key not in {"CODEX_THREAD_ID", "PYTHONPATH", "PYTHONHOME"}}
        # Hook-local Git variables must not redirect tests that create their
        # own repositories back into the user's checkout.
        for key in git(root, "rev-parse", "--local-env-vars").stdout.splitlines():
            env.pop(key, None)
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1",
                   AUTOSWARM_HOME=str(work / "state"), CURSOR_CONFIG_DIR=str(work / "native-cursor"))
        environment = work / "venv"
        print(f"Checking {root}\nPython: {sys.version.split()[0]} ({sys.platform})\nCreating an isolated test environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
        binary = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binary / ("python.exe" if os.name == "nt" else "python")
        cli = binary / ("autoswarm.exe" if os.name == "nt" else "autoswarm")
        env["PATH"] = str(binary) + os.pathsep + env.get("PATH", "")
        wheels = work / "dist"
        steps = [
            ("Build wheel", [python, "-m", "pip", "wheel", "--no-deps", ".", "--wheel-dir", wheels]),
            ("Install built wheel", [python, "-m", "pip", "install", "--no-index", "--find-links", wheels, "autoswarm"]),
            ("CLI version", [cli, "--version"]),
            ("CLI help", [cli, "--help"]),
            ("Module entry point", [python, "-m", "autoswarm", "--version"]),
            ("Python tests", [python, "-m", "unittest", "discover", "-s", "tests", "-v"]),
            ("OpenCode plugin tests", [node, "--test", "tests/test_opencode_plugin.mjs"]),
            ("Relocatable skill", [cli, "setup", "--host", "codex", "--skill-dir", work / "autoswarm-skill"]),
            ("Installed distribution outside checkout", [python, "scripts/check_distribution.py", "--", cli]),
        ]
        for label, argv in steps:
            run_step(label, argv, root, env)
        print("\nAll local CI checks passed. The remote OS/Python matrix still runs on GitHub.", flush=True)
    finally:
        try:
            shutil.rmtree(work)
        except OSError:
            # A failed test can leave a Windows runner exiting. Keep the
            # original failure rather than replacing it with a cleanup error.
            print(f"Temporary check files retained at {work}; a test process may still be exiting", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install-hook", action="store_true", help="Install a repository-local pre-push hook using this Python")
    mode.add_argument("--pre-push", action="store_true", help="Read Git push updates from stdin and check the pushed commit")
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 10):
            raise CheckError("Python 3.10 or later is required")
        if args.install_hook:
            install_hook(ROOT)
        elif args.pre_push:
            updates = sys.stdin.read()
            if validate_push(ROOT, updates):
                run_checks(ROOT)
                # Checks can take a while. Edits or a new commit made during
                # the run must not give the original push a false pass.
                validate_push(ROOT, updates)
            else:
                print("No new commits to check.")
        else:
            run_checks(ROOT)
        return 0
    except CheckError as error:
        print(f"\nCHECK FAILED: {error}", file=sys.stderr)
        return error.code
    except OSError as error:
        print(f"\nCHECK FAILED: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nChecks cancelled; push blocked.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
