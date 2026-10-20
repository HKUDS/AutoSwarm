"""Session mapping helpers for external channels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SessionRoute:
    session_id: str
    chat_id: str
    thread_id: str = ""
    reply_to: str = ""


class ChannelSessionMapping:
    @staticmethod
    def derive(
        *,
        channel: str,
        chat_id: str,
        thread_id: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRoute:
        meta = metadata or {}
        resolved_chat = str(chat_id or meta.get("chat_id") or "")
        resolved_thread = str(thread_id or meta.get("thread_id") or "")
        resolved_reply = str(reply_to or meta.get("reply_to") or "")
        if resolved_thread:
            session_id = f"{channel}:{resolved_chat}:{resolved_thread}"
        else:
            session_id = f"{channel}:{resolved_chat}"
        return SessionRoute(
            session_id=session_id,
            chat_id=resolved_chat,
            thread_id=resolved_thread,
            reply_to=resolved_reply,
        )
