"""Local checks must stop a real push on failure and validate the pushed code."""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("local_checks", Path(__file__).resolve().parents[1] / "scripts/check.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


@unittest.skipUnless(shutil.which("git"), "Git is required for hook integration tests")
class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo with ' quotes"
        self.repo.mkdir()
        env = dict(os.environ)
        for key in checks.git(checks.ROOT, "rev-parse", "--local-env-vars").stdout.splitlines():
            env.pop(key, None)
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Hook test")
        self.git("config", "user.email", "hook-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "scripts").mkdir()
        self.script = self.repo / "scripts/check.py"
        self.script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        self.commit()

    def git(self, *args, check=True, root=None):
        return checks.git(root or self.repo, *args, check=check)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "fixture")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def update(self, commit=None):
        return f"refs/heads/main {commit or self.git('rev-parse', 'HEAD').stdout.strip()} refs/heads/main {'0' * 40}\n"

    def install(self):
        with contextlib.redirect_stdout(io.StringIO()):
            checks.install_hook(self.repo)

    def remote(self):
        remote = Path(self.tmp.name) / "remote.git"
        self.git("init", "--bare", "--quiet", str(remote))
        return remote

    def simulated_checks(self, fail=False):
        self.script.write_text(
            "import importlib.util, sys\nfrom pathlib import Path\n"
            f"spec = importlib.util.spec_from_file_location('checks', {str(checks.ROOT / 'scripts/check.py')!r})\n"
            "checks = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(checks)\n"
            "checks.ROOT = Path(__file__).resolve().parents[1]\n"
            "def run(root):\n"
            + ("    raise checks.CheckError('synthetic check failure', 7)\n" if fail else "    print('synthetic check success')\n")
            + "checks.run_checks = run\nsys.exit(checks.main())\n", encoding="utf-8")
        self.commit()

    def test_failed_check_blocks_a_real_local_push_before_ref_is_written(self):
        self.simulated_checks(fail=True)
        self.install()
        remote = self.remote()
        pushed = self.git("push", remote.as_posix(), "HEAD:refs/heads/main", check=False)
        self.assertNotEqual(pushed.returncode, 0)
        self.assertIn("synthetic check failure", pushed.stdout + pushed.stderr)
        self.assertNotEqual(self.git("rev-parse", "--verify", "refs/heads/main", root=remote, check=False).returncode, 0)

    def test_successful_check_allows_a_real_local_push(self):
        self.simulated_checks()
        self.install()
        remote = self.remote()
        self.git("push", remote.as_posix(), "HEAD:refs/heads/main")
        self.assertEqual(self.git("rev-parse", "refs/heads/main", root=remote).stdout,
                         self.git("rev-parse", "HEAD").stdout)

    def test_dirty_checkout_blocks_before_any_expensive_checks(self):
        (self.repo / "uncommitted.txt").write_text("not pushed", encoding="utf-8")
        with patch.object(checks, "ROOT", self.repo), patch("sys.stdin", io.StringIO(self.update())), \
                patch.object(checks, "run_checks") as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(checks.main(["--pre-push"]), 1)
            run.assert_not_called()

    def test_other_commit_cannot_pass_checks_for_the_current_checkout(self):
        previous = self.git("rev-parse", "HEAD").stdout.strip()
        self.script.write_text("print('new version')\n", encoding="utf-8")
        self.commit()
        with self.assertRaisesRegex(checks.CheckError, "exact code"):
            checks.validate_push(self.repo, self.update(previous))

    def test_edits_made_during_checks_block_the_original_push(self):
        def edit(root):
            (root / "edited-during-checks.txt").write_text("new work", encoding="utf-8")
        with patch.object(checks, "ROOT", self.repo), patch("sys.stdin", io.StringIO(self.update())), \
                patch.object(checks, "run_checks", side_effect=edit), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(checks.main(["--pre-push"]), 1)

    def test_clean_head_and_annotated_tag_are_accepted(self):
        self.assertTrue(checks.validate_push(self.repo, self.update()))
        self.git("tag", "-a", "fixture", "-m", "fixture")
        tag = self.git("rev-parse", "refs/tags/fixture").stdout.strip()
        self.assertTrue(checks.validate_push(self.repo, self.update(tag)))

    def test_deletion_only_push_skips_checks_but_malformed_updates_do_not(self):
        with patch.object(checks, "git") as command:
            self.assertFalse(checks.validate_push(self.repo, self.update("0" * 40)))
            self.assertFalse(checks.validate_push(self.repo, ""))
            command.assert_not_called()
        with self.assertRaises(checks.CheckError):
            checks.validate_push(self.repo, "not a push update")

    def test_hook_install_is_idempotent_and_preserves_custom_hooks(self):
        self.install()
        self.install()
        hook = self.repo / ".git/hooks/pre-push"
        custom = "#!/bin/sh\necho custom hook\n"
        hook.write_text(custom, encoding="utf-8")
        with self.assertRaisesRegex(checks.CheckError, "left intact"):
            self.install()
        self.assertEqual(hook.read_text(encoding="utf-8"), custom)

    def test_configured_hooks_directory_is_preserved(self):
        self.git("config", "core.hooksPath", "custom-hooks")
        with self.assertRaisesRegex(checks.CheckError, "already configured"):
            self.install()
        self.assertFalse((self.repo / "custom-hooks").exists())

    def test_failed_python_suite_stops_pipeline_and_isolates_hook_environment(self):
        seen = []
        def step(label, argv, root, env):
            seen.append(label)
            self.assertNotIn("CODEX_THREAD_ID", env)
            self.assertNotIn("AUTOSWARM_PARENT", env)
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("GIT_DIR", env)
            if label == "Python tests":
                raise checks.CheckError("synthetic Python failure", 7)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "fixture-thread", "AUTOSWARM_PARENT": "fixture-parent", "PYTHONPATH": "fixture-path"}), \
                patch.object(checks.shutil, "which", return_value="node"), \
                patch.object(checks.venv, "EnvBuilder"), patch.object(checks, "run_step", side_effect=step), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(checks.CheckError) as error:
                checks.run_checks(self.repo)
        self.assertEqual(error.exception.code, 7)
        self.assertEqual(seen[-1], "Python tests")
        self.assertNotIn("OpenCode plugin tests", seen)
        self.assertNotIn("Installed distribution outside checkout", seen)

    def test_failed_child_command_keeps_its_exit_code_and_stage(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(checks.CheckError, "fixture check failed") as error:
                checks.run_step("fixture check", [sys.executable, "-c", "raise SystemExit(7)"], self.repo, dict(os.environ))
        self.assertEqual(error.exception.code, 7)


if __name__ == "__main__":
    unittest.main()
