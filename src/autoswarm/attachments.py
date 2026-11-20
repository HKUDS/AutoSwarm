"""Images and files the user attaches, kept with the AutoSwarm records.

Each attach copies the files into <home>/attachments/<id>/, so a conversation keeps what was sent
even when the original moves (as Codex and DSH keep their own copies). Agents are told the saved
paths and open files with their own tools when needed; an agent that accepts images (Claude Code,
Codex, OpenCode, ACP agents advertising image prompts) also receives each image itself.

A task or message that mentions a saved attachment's path carries that attachment: a main agent
hands an image to a worker by naming its path, the way agents already refer to files.
"""
from __future__ import annotations

import base64
import mimetypes
import re
import shutil
import uuid
from pathlib import Path

from .records import SwarmError

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE = 10 * 1024 * 1024
MAX_FILE = 50 * 1024 * 1024
MAX_COUNT = 10
FOLDER = re.compile(r"attachments[\\/]+([0-9a-f]{32})[\\/]+")


def describe(path: Path) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {"path": str(path), "name": path.name, "mime": mime, "kind": "image" if mime in IMAGE_TYPES else "file",
            "size": path.stat().st_size}


def store(records, paths) -> list[dict]:
    """Copy the user's files into the records; nothing is stored when one of them is refused."""
    paths = [Path(value).expanduser() for value in paths or []]
    if len(paths) > MAX_COUNT:
        raise SwarmError("too_many_attachments", f"Attach at most {MAX_COUNT} files at once")
    for path in paths:
        if not path.is_file():
            raise SwarmError("attachment_not_found", f"Not a file: {path}")
        item = describe(path)
        limit = MAX_IMAGE if item["kind"] == "image" else MAX_FILE
        if item["size"] > limit:
            raise SwarmError("attachment_too_large", f"{path.name} is larger than {limit // (1024 * 1024)} MB")
    if not paths:
        return []
    folder = records.home / "attachments" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    stored = []
    for index, path in enumerate(paths):
        target = folder / path.name
        if target.exists():  # Two files of the same name keep both.
            target = folder / f"{index}-{path.name}"
        shutil.copyfile(path, target)
        stored.append(describe(target))
    return stored


def mentioned(records, text: str) -> list[dict]:
    """Saved attachments whose path appears in a task or message (any slash style, quoted or not)."""
    if not text or "attachments" not in text:
        return []
    plain = text.replace("\\\\", "\\")  # Paths copied out of JSON keep doubled backslashes.
    root = records.home / "attachments"
    found = []
    for identity in dict.fromkeys(FOLDER.findall(plain)):
        folder = root / identity
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.is_file() and any(f"{identity}{sep}{path.name}" in plain for sep in ("\\", "/")):
                found.append(describe(path))
    return found


def merge(*lists) -> list[dict]:
    return list({item["path"]: item for items in lists for item in items or []}.values())


def note(items) -> str:
    """What the agent reads about the attachments it receives."""
    if not items:
        return ""
    lines = [f"- {item['name']}: {item['path']} ({item['mime']}, {item['size']} bytes)" for item in items]
    return ("\n\nAttached by the user and saved locally (images are also attached to this message when you accept "
            "images; open other files with your tools as needed). To give a worker one of them, include its full "
            "path in the worker's task or message:\n" + "\n".join(lines))


def images(items) -> list[dict]:
    return [item for item in items or [] if item.get("kind") == "image" and Path(item["path"]).is_file()]


def data(item) -> str:
    return base64.b64encode(Path(item["path"]).read_bytes()).decode("ascii")


def folders(items) -> list[str]:
    return sorted({str(Path(item["path"]).parent) for item in items or []})
