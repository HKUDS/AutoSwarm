from __future__ import annotations

import shutil
import uuid
from pathlib import Path


def workspace_tmp_root() -> Path:
    root = Path.cwd() / ".tmp-test" / "portable"
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_path(*parts: str, base: str = "shared") -> Path:
    root = workspace_tmp_root() / base
    root.mkdir(parents=True, exist_ok=True)
    return root.joinpath(*parts)


class WorkspaceTemporaryDirectory:
    def __init__(self, suffix: str | None = None, prefix: str | None = None, dir: str | None = None) -> None:
        _ = dir
        stem = f"{prefix or 'tmp'}{uuid.uuid4().hex}{suffix or ''}"
        self._path = workspace_tmp_root() / stem
        self.name = str(self._path)

    def __enter__(self) -> str:
        self._path.mkdir(parents=True, exist_ok=True)
        return self.name

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        shutil.rmtree(self._path, ignore_errors=True)
