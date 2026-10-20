"""Local worktree helpers for runtime-managed subagents."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from opc.core.config import OPCConfig
from opc.layer4_tools.execution_context import build_task_execution_context, venv_python_path


async def create_worktree(
    base_path: str | None,
    *,
    config: OPCConfig | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    source = Path(base_path or os.getcwd()).resolve()
    root = await _find_git_root(source)
    temp_dir = Path(tempfile.mkdtemp(prefix="opc-native-v2-"))

    if root is not None:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(root),
            "worktree",
            "add",
            "--detach",
            str(temp_dir),
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            info = {
                "path": str(temp_dir),
                "git_root": str(root),
                "mode": "git_worktree",
                "stdout": stdout.decode("utf-8", errors="replace"),
            }
            return await _prepare_execution_environment(info, config=config, on_progress=on_progress)
        shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="opc-native-v2-copy-"))

    shutil.copytree(
        source,
        temp_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"),
    )
    info = {
        "path": str(temp_dir),
        "git_root": str(root) if root else "",
        "mode": "copy",
    }
    return await _prepare_execution_environment(info, config=config, on_progress=on_progress)


async def cleanup_worktree(info: dict[str, Any] | None) -> None:
    if not info:
        return
    path = Path(str(info.get("path", "") or "")).resolve()
    mode = str(info.get("mode", "") or "")
    git_root = str(info.get("git_root", "") or "")
    if not path.exists():
        return
    if mode == "git_worktree" and git_root:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            git_root,
            "worktree",
            "remove",
            "--force",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return
    shutil.rmtree(path, ignore_errors=True)


async def _find_git_root(path: Path) -> Path | None:
    current = path
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


async def _prepare_execution_environment(
    info: dict[str, Any],
    *,
    config: OPCConfig | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    workspace = Path(str(info.get("path", "") or "")).resolve()
    info["execution_context"] = build_task_execution_context(workspace_root=workspace, config=config)
    if config is None:
        return info
    venv_cfg = config.system.native_runtime.execution_environment.worktree_venv
    if not venv_cfg.enabled:
        return info

    provider = _resolve_venv_provider(venv_cfg.provider)
    venv_path = workspace / str(venv_cfg.venv_dir or ".opc-venv")
    python_path = venv_python_path(venv_path)
    try:
        if not python_path.exists():
            create_args = _venv_create_args(provider, venv_path, system_site_packages=venv_cfg.system_site_packages)
            await _run_process(create_args, cwd=workspace, on_progress=on_progress)
        sync_commands = _build_sync_commands(
            workspace=workspace,
            python_path=python_path,
            provider=provider,
            editable_project=venv_cfg.editable_project,
            requirements_files=_detect_requirements_files(
                workspace=workspace,
                configured=venv_cfg.requirements_files,
                auto_detect=venv_cfg.auto_detect_requirements,
            ),
        )
        for command in sync_commands:
            await _run_process(command, cwd=workspace, on_progress=on_progress)
        info.update(
            {
                "venv_path": str(venv_path),
                "python_executable": str(python_path),
                "venv_provider": provider,
                "environment_prepared": True,
                "environment_sync_commands": len(sync_commands),
            }
        )
        info["execution_context"] = build_task_execution_context(
            workspace_root=workspace,
            config=config,
            venv_path=venv_path,
            python_executable=python_path,
            venv_provider=provider,
        )
    except Exception as exc:
        error = str(exc)
        info.update(
            {
                "venv_path": str(venv_path),
                "python_executable": str(python_path),
                "venv_provider": provider,
                "environment_prepared": False,
                "environment_error": error,
            }
        )
        info["execution_context"] = build_task_execution_context(
            workspace_root=workspace,
            config=config,
            venv_path=venv_path,
            python_executable=python_path,
            venv_provider=provider,
            preparation_error=error,
        )
        if venv_cfg.fail_if_prepare_fails:
            raise
    return info


def _resolve_venv_provider(provider: str) -> str:
    normalized = str(provider or "auto").strip().lower() or "auto"
    if normalized == "uv":
        return "uv"
    if normalized == "venv":
        return "venv"
    return "uv" if shutil.which("uv") else "venv"


def _venv_create_args(provider: str, venv_path: Path, *, system_site_packages: bool) -> list[str]:
    if provider == "uv":
        return ["uv", "venv", str(venv_path), "--python", sys.executable]
    args = [sys.executable, "-m", "venv", str(venv_path)]
    if system_site_packages:
        args.append("--system-site-packages")
    return args


def _detect_requirements_files(
    *,
    workspace: Path,
    configured: list[str],
    auto_detect: bool,
) -> list[Path]:
    candidates: list[Path] = []
    for entry in configured:
        text = str(entry or "").strip()
        if text:
            candidates.append((workspace / text).resolve())
    if auto_detect:
        for name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt", "requirements-ci.txt"):
            candidate = (workspace / name).resolve()
            if candidate.exists():
                candidates.append(candidate)
    seen: set[str] = set()
    resolved: list[Path] = []
    for item in candidates:
        key = str(item)
        if key in seen or not item.exists():
            continue
        seen.add(key)
        resolved.append(item)
    return resolved


def _build_sync_commands(
    *,
    workspace: Path,
    python_path: Path,
    provider: str,
    editable_project: bool,
    requirements_files: list[Path],
) -> list[list[str]]:
    commands: list[list[str]] = []
    if editable_project and (workspace / "pyproject.toml").exists():
        if provider == "uv" and shutil.which("uv"):
            commands.append(["uv", "pip", "install", "--python", str(python_path), "-e", str(workspace)])
        else:
            commands.append([str(python_path), "-m", "pip", "install", "-e", str(workspace)])
    for item in requirements_files:
        if provider == "uv" and shutil.which("uv"):
            commands.append(["uv", "pip", "install", "--python", str(python_path), "-r", str(item)])
        else:
            commands.append([str(python_path), "-m", "pip", "install", "-r", str(item)])
    return commands


async def _run_process(args: list[str], *, cwd: Path, on_progress: Any = None) -> None:
    if on_progress:
        try:
            await on_progress(f"[worktree-env] {' '.join(args[:4])}")
        except TypeError:
            await on_progress(f"[worktree-env] {' '.join(args[:4])}")
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    detail = stderr_text or stdout_text or f"exit code {proc.returncode}"
    raise RuntimeError(f"{' '.join(args[:4])} failed: {detail}")
