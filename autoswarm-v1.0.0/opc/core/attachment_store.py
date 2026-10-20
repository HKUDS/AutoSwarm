"""Attachment storage layer — disk-based file storage with lightweight references.

Files are stored on disk under `{opc_home}/projects/{project_id}/attachments/{id}/`.
Only lightweight AttachmentRef objects (no binary content) flow through the engine
pipeline, databases, and WebSocket messages.  Base64 encoding is performed lazily
at the two endpoints: ingestion (decode → disk) and LLM call (disk → encode).
"""

from __future__ import annotations

import base64
import mimetypes
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_MIME_PREFIXES = (
    "image/",
    "text/",
    "application/pdf",
    "application/json",
    "application/x-yaml",
    "application/yaml",
)
ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff",
    ".txt", ".md", ".pdf", ".csv", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".java", ".c", ".cpp", ".h", ".go", ".rs", ".rb", ".sh",
    ".xml", ".toml", ".ini", ".cfg", ".log",
    ".docx", ".xlsx", ".pptx",
    ".mp4", ".mpeg", ".mpg", ".mov", ".webm",
}
MAX_FILE_SIZE = 10 * 1024 * 1024       # 10 MB per file
MAX_TOTAL_SIZE = 20 * 1024 * 1024      # 20 MB per message

ALLOWED_MIME_TYPES = {
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/webm",
}

_MIME_EXTENSION_OVERRIDES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/json": ".json",
    "application/x-yaml": ".yaml",
    "application/yaml": ".yaml",
    "video/mp4": ".mp4",
    "video/mpeg": ".mpeg",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}


@dataclass
class AttachmentRef:
    """Lightweight reference to a stored attachment — safe for JSON / metadata."""

    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int
    disk_path: str  # relative to opc_home

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "disk_path": self.disk_path,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AttachmentRef:
        return cls(
            attachment_id=d["attachment_id"],
            filename=d["filename"],
            mime_type=d.get("mime_type", "application/octet-stream"),
            size_bytes=d.get("size_bytes", 0),
            disk_path=d.get("disk_path", ""),
        )


def _sanitize_filename(name: str) -> str:
    """Strip path components and dangerous characters from a filename."""
    name = os.path.basename(name)
    name = name.replace("..", "").replace("/", "").replace("\\", "")
    return name or "upload"


def _check_mime(filename: str, mime: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    if ext in ALLOWED_EXTENSIONS:
        return True
    if mime in ALLOWED_MIME_TYPES:
        return True
    for prefix in ALLOWED_MIME_PREFIXES:
        if mime.startswith(prefix):
            return True
    return False


def _normalize_mime(mime_type: str | None) -> str:
    return str(mime_type or "").strip().lower()


def _split_data_url(payload: str) -> tuple[str, str | None]:
    raw = str(payload or "").strip()
    if not raw.startswith("data:"):
        return raw, None
    header, sep, encoded = raw.partition(",")
    if not sep:
        return raw, None
    mime = header[5:].split(";", 1)[0].strip().lower()
    return encoded, mime or None


def _extension_for_mime(mime_type: str) -> str:
    if not mime_type:
        return ""
    override = _MIME_EXTENSION_OVERRIDES.get(mime_type)
    if override:
        return override
    guessed = mimetypes.guess_extension(mime_type, strict=False) or ""
    return guessed.lower()


def _ensure_filename_extension(filename: str, mime_type: str) -> str:
    if os.path.splitext(filename)[1]:
        return filename
    extension = _extension_for_mime(mime_type)
    if extension:
        return f"{filename}{extension}"
    return filename


class AttachmentStore:
    """Manages attachment lifecycle on disk."""

    def __init__(self, opc_home: Path, project_id: str) -> None:
        self.opc_home = opc_home
        self.project_id = project_id
        self.base_dir = opc_home / "projects" / project_id / "attachments"

    def _ensure_dir(self, attachment_id: str) -> Path:
        d = self.base_dir / attachment_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def save_from_base64(
        self,
        filename: str,
        b64_data: str,
        mime_type: str | None = None,
    ) -> AttachmentRef:
        """Decode base64 data and persist to disk.  Returns a lightweight ref."""
        filename = _sanitize_filename(filename)
        b64_payload, inferred_mime = _split_data_url(b64_data)
        mime = _normalize_mime(mime_type) or inferred_mime or ""
        if not mime:
            guessed_mime, _ = mimetypes.guess_type(filename)
            mime = guessed_mime or "application/octet-stream"
        filename = _ensure_filename_extension(filename, mime)
        if mime == "application/octet-stream":
            guessed_mime, _ = mimetypes.guess_type(filename)
            mime = guessed_mime or mime
        try:
            raw = base64.b64decode(b64_payload)
        except Exception as exc:
            raise ValueError(f"Invalid base64 data for {filename}: {exc}") from exc
        size = len(raw)
        if size > MAX_FILE_SIZE:
            raise ValueError(f"File too large: {size} bytes (limit {MAX_FILE_SIZE})")

        if not _check_mime(filename, mime):
            raise ValueError(f"Unsupported file type: {filename} ({mime})")

        aid = uuid.uuid4().hex[:16]
        dest_dir = self._ensure_dir(aid)
        dest = dest_dir / filename
        dest.write_bytes(raw)

        rel = dest.relative_to(self.opc_home)
        return AttachmentRef(
            attachment_id=aid,
            filename=filename,
            mime_type=mime,
            size_bytes=size,
            disk_path=str(rel),
        )

    async def save_from_path(self, file_path: Path) -> AttachmentRef:
        """Copy a local file into the attachment store. Used by CLI."""
        file_path = file_path.expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        size = file_path.stat().st_size
        if size > MAX_FILE_SIZE:
            raise ValueError(f"File too large: {size} bytes (limit {MAX_FILE_SIZE})")

        filename = _sanitize_filename(file_path.name)
        mime, _ = mimetypes.guess_type(filename)
        mime = mime or "application/octet-stream"
        if not _check_mime(filename, mime):
            raise ValueError(f"Unsupported file type: {filename} ({mime})")

        aid = uuid.uuid4().hex[:16]
        dest_dir = self._ensure_dir(aid)
        dest = dest_dir / filename
        shutil.copy2(str(file_path), str(dest))

        rel = dest.relative_to(self.opc_home)
        return AttachmentRef(
            attachment_id=aid,
            filename=filename,
            mime_type=mime,
            size_bytes=size,
            disk_path=str(rel),
        )

    def resolve_abs_path(self, ref: AttachmentRef) -> Path:
        """Return the absolute path on disk for reading."""
        resolved = (self.opc_home / ref.disk_path).resolve()
        if not str(resolved).startswith(str(self.base_dir.resolve())):
            raise ValueError(f"Path traversal detected: {ref.disk_path}")
        return resolved

    def read_bytes(self, ref: AttachmentRef) -> bytes:
        """Read raw file content from disk."""
        return self.resolve_abs_path(ref).read_bytes()

    def read_base64(self, ref: AttachmentRef) -> str:
        """Read file and return as base64 string — called only at LLM call time."""
        return base64.b64encode(self.read_bytes(ref)).decode("ascii")

    def resolve_http_path(self, ref: AttachmentRef) -> str:
        """Return the HTTP-accessible path for frontend display."""
        return f"/api/attachments/{ref.attachment_id}/{ref.filename}"
