from __future__ import annotations

import asyncio
import contextlib
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from opc.core.config import OPCConfig
from opc.core.models import Task
from opc.layer3_agent.runtime_v2.worktree import _prepare_execution_environment
from opc.layer4_tools.execution_context import ensure_task_execution_context, venv_python_path
from opc.layer4_tools.python_exec import python_exec
from opc.layer4_tools.shell import shell_exec


@contextlib.contextmanager
def _workspace_tempdir() -> Path:
    base = Path.cwd() / ".tmp-test" / f"runtime-exec-env-{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


class _FakeStream:
    def __init__(self, lines: list[str] | None = None) -> None:
        encoded = [(line if line.endswith("\n") else f"{line}\n").encode("utf-8") for line in (lines or [])]
        self._buffer = b"".join(encoded)

    async def readline(self) -> bytes:
        if not self._buffer:
            return b""
        newline_index = self._buffer.find(b"\n")
        if newline_index < 0:
            chunk = self._buffer
            self._buffer = b""
            return chunk
        chunk = self._buffer[: newline_index + 1]
        self._buffer = self._buffer[newline_index + 1 :]
        return chunk

    async def read(self, limit: int = -1) -> bytes:
        if not self._buffer:
            return b""
        if limit < 0:
            chunk = self._buffer
            self._buffer = b""
            return chunk
        chunk = self._buffer[:limit]
        self._buffer = self._buffer[limit:]
        return chunk


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout_lines: list[str] | None = None,
        stderr_lines: list[str] | None = None,
        returncode: int = 0,
        communicate_stdout: str = "",
        communicate_stderr: str = "",
    ) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(stderr_lines)
        self.returncode = returncode
        self._communicate_stdout = communicate_stdout
        self._communicate_stderr = communicate_stderr
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return (
            self._communicate_stdout.encode("utf-8"),
            self._communicate_stderr.encode("utf-8"),
        )

    def kill(self) -> None:
        self.killed = True


class ExecutionEnvironmentContextTests(unittest.TestCase):
    def test_ensure_task_execution_context_seeds_workspace_and_sandbox(self) -> None:
        config = OPCConfig()
        config.system.native_runtime.execution_environment.sandbox.enabled = True
        task = Task(
            metadata={
                "workspace_root": "/tmp/demo-workspace",
                "output_root": "/tmp/demo-workspace/project-a",
                "target_output_dir": "/tmp/demo-workspace/project-a",
                "comms_root": "/tmp/demo-workspace/.opc-comms",
            }
        )

        context = ensure_task_execution_context(task, config)

        self.assertEqual(context["workspace_root"], str(Path("/tmp/demo-workspace").resolve()))
        self.assertEqual(context["output_root"], str(Path("/tmp/demo-workspace/project-a").resolve()))
        self.assertEqual(context["comms_root"], str(Path("/tmp/demo-workspace/.opc-comms").resolve()))
        self.assertIn("sandbox", context)
        self.assertEqual(task.metadata["_execution_context"]["workspace_root"], context["workspace_root"])


class WorktreeEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_execution_environment_creates_uv_venv_and_syncs_project(self) -> None:
        with _workspace_tempdir() as workspace:
            (workspace / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.1'\n", encoding="utf-8")
            (workspace / "requirements.txt").write_text("pytest\n", encoding="utf-8")

            config = OPCConfig()
            venv_cfg = config.system.native_runtime.execution_environment.worktree_venv
            venv_cfg.enabled = True
            venv_cfg.provider = "uv"
            venv_cfg.venv_dir = ".rt-venv"

            commands: list[list[str]] = []

            async def _record(args: list[str], *, cwd: Path, on_progress=None) -> None:
                _ = (cwd, on_progress)
                commands.append(list(args))

            with patch("opc.layer3_agent.runtime_v2.worktree._run_process", AsyncMock(side_effect=_record)):
                info = await _prepare_execution_environment(
                    {"path": str(workspace), "mode": "copy", "git_root": ""},
                    config=config,
                )

            self.assertEqual(commands[0][0:2], ["uv", "venv"])
            self.assertIn("--python", commands[0])
            self.assertEqual(commands[1][0:4], ["uv", "pip", "install", "--python"])
            self.assertIn("-e", commands[1])
            self.assertEqual(commands[2][0:4], ["uv", "pip", "install", "--python"])
            self.assertIn("-r", commands[2])
            self.assertTrue(info["venv_path"].endswith(".rt-venv"))
            self.assertEqual(info["execution_context"]["venv_provider"], "uv")


class ToolExecutionEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_shell_exec_wraps_command_with_sandbox_and_virtualenv(self) -> None:
        with _workspace_tempdir() as workspace:
            venv = workspace / ".venv"
            python_path = venv_python_path(venv)
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("", encoding="utf-8")

            task = Task(
                metadata={
                    "target_output_dir": str(workspace),
                    "_execution_context": {
                        "workspace_root": str(workspace),
                        "venv_path": str(venv),
                        "python_executable": str(python_path),
                        "venv_provider": "uv",
                        "sandbox": {
                            "platform": "linux",
                            "mode": "workspace-write",
                            "wrapper": "bwrap",
                            "allow_network": False,
                            "fail_if_unavailable": False,
                            "allow_direct_fallback": True,
                        },
                    },
                }
            )
            captured: dict[str, object] = {}

            async def _spawn(*args, **kwargs):
                captured["args"] = list(args)
                captured["kwargs"] = dict(kwargs)
                return _FakeProcess(stdout_lines=["ok"], stderr_lines=[])

            with patch("opc.layer4_tools.shell.asyncio.create_subprocess_exec", side_effect=_spawn):
                result = await shell_exec("pytest -q", task=task, shell="powershell")

            args = list(captured["args"])
            env = dict(captured["kwargs"]["env"])
            self.assertEqual(args[0], "bwrap")
            self.assertIn("--unshare-net", args)
            self.assertIn("powershell", " ".join(args).lower())
            self.assertEqual(env["VIRTUAL_ENV"], str(venv))
            self.assertTrue(env["PATH"].startswith(str(python_path.parent)))
            self.assertEqual(result["sandbox"]["effective_wrapper"], "bwrap")

    async def test_python_exec_prefers_context_python(self) -> None:
        with _workspace_tempdir() as workspace:
            venv = workspace / ".venv"
            python_path = venv_python_path(venv)
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("", encoding="utf-8")

            task = Task(
                metadata={
                    "target_output_dir": str(workspace),
                    "_execution_context": {
                        "workspace_root": str(workspace),
                        "venv_path": str(venv),
                        "python_executable": str(python_path),
                        "sandbox": {
                            "platform": "windows",
                            "mode": "elevated",
                            "wrapper": "none",
                            "allow_network": True,
                            "fail_if_unavailable": False,
                            "allow_direct_fallback": True,
                        },
                    },
                }
            )
            captured: dict[str, object] = {}

            async def _spawn(*args, **kwargs):
                captured["args"] = list(args)
                captured["kwargs"] = dict(kwargs)
                return _FakeProcess(communicate_stdout="42", communicate_stderr="")

            with patch("opc.layer4_tools.python_exec.asyncio.create_subprocess_exec", side_effect=_spawn):
                result = await python_exec("print(42)", task=task)

            args = list(captured["args"])
            self.assertEqual(args[0], str(python_path))
            self.assertEqual(Path(captured["kwargs"]["cwd"]).resolve(), workspace.resolve())
            self.assertEqual(result["stdout"], "42")

    async def test_shell_exec_handles_long_single_line_output(self) -> None:
        with _workspace_tempdir() as workspace:
            task = Task(metadata={"target_output_dir": str(workspace)})

            async def _spawn(*args, **kwargs):
                _ = (args, kwargs)
                return _FakeProcess(stdout_lines=["x" * 70000], stderr_lines=[])

            with patch("opc.layer4_tools.shell.asyncio.create_subprocess_exec", side_effect=_spawn):
                result = await shell_exec("printf test", task=task, shell="bash")

            self.assertTrue(result["success"])
            self.assertEqual(len(result["stdout"]), 50000)
            self.assertTrue(str(result["stdout"]).startswith("x" * 100))

    async def test_shell_exec_falls_back_to_workspace_root_when_output_root_is_missing(self) -> None:
        with _workspace_tempdir() as workspace:
            missing_output = workspace / "deliverables"
            task = Task(
                metadata={
                    "workspace_root": str(workspace),
                    "target_output_dir": str(missing_output),
                }
            )
            captured: dict[str, object] = {}

            async def _spawn(*args, **kwargs):
                captured["args"] = list(args)
                captured["kwargs"] = dict(kwargs)
                return _FakeProcess(stdout_lines=["ok"], stderr_lines=[])

            with patch("opc.layer4_tools.shell.asyncio.create_subprocess_exec", side_effect=_spawn):
                result = await shell_exec("pwd", task=task, shell="bash")

            self.assertTrue(result["success"])
            self.assertEqual(Path(captured["kwargs"]["cwd"]).resolve(), workspace.resolve())

    async def test_shell_exec_returns_structured_error_for_missing_explicit_working_directory(self) -> None:
        with _workspace_tempdir() as workspace:
            task = Task(metadata={"workspace_root": str(workspace)})

            result = await shell_exec(
                "pwd",
                task=task,
                shell="bash",
                working_directory=str(workspace / "missing"),
            )

            self.assertFalse(result["success"])
            self.assertIn("Working directory does not exist", str(result["error"]))


if __name__ == "__main__":
    unittest.main()
