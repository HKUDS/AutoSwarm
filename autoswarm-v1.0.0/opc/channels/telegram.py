from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from opc.channels.provider_base import PollingChannel
from opc.core.models import SystemMessage

_TELEGRAM_MAX_MESSAGE_LEN = 4000


class TelegramChannel(PollingChannel):
    name = "telegram"
    required_package = "telegram"

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self._bot: Any = None
        self._update_offset: int = 0

    def get_required_config_fields(self) -> list[str]:
        return ["token"]

    def get_poll_interval_seconds(self) -> float:
        return 0.25

    def normalize_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = payload.get("message", payload)
        chat = message.get("chat", {})
        user = message.get("from", {})
        attachments = []
        for key in ("photo", "document", "audio", "voice", "video"):
            if message.get(key):
                attachments.append({"type": key, "value": message.get(key)})
        text = str(message.get("text", "") or message.get("caption", "") or "")
        return {
            "sender_id": str(user.get("id", "")),
            "chat_id": str(chat.get("id", "")),
            "content": text,
            "thread_id": str(message.get("message_thread_id", "") or ""),
            "reply_to": str((message.get("reply_to_message") or {}).get("message_id", "") or ""),
            "attachments": attachments,
            "metadata": {
                "message_id": str(message.get("message_id", "") or ""),
                "chat_type": str(chat.get("type", "") or ""),
            },
        }

    async def _ensure_bot(self) -> Any:
        if self._bot is not None:
            return self._bot
        from telegram import Bot

        self._bot = Bot(token=self.config.token)
        return self._bot

    async def poll_once(self) -> None:
        bot = await self._ensure_bot()
        updates = await bot.get_updates(
            offset=self._update_offset or None,
            timeout=20,
            allowed_updates=["message"],
        )
        for update in updates:
            self._update_offset = max(self._update_offset, int(update.update_id) + 1)
            message = getattr(update, "message", None)
            if message is None:
                continue
            payload = self.normalize_update(update.to_dict())
            await self.publish_normalized(payload)

    async def send(self, message: SystemMessage) -> None:
        await super().send(message)
        bot = await self._ensure_bot()
        metadata = dict(message.metadata or {})
        chat_id = int(str(metadata.get("chat_id") or message.session_id))
        thread_id = str(metadata.get("thread_id", "") or "")
        reply_to = str(metadata.get("reply_to", "") or "")
        reply_to_message_id = None
        if reply_to:
            try:
                reply_to_message_id = int(reply_to)
            except ValueError:
                reply_to_message_id = None
        for chunk in _split_message(message.content or "", _TELEGRAM_MAX_MESSAGE_LEN):
            kwargs: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if thread_id:
                try:
                    kwargs["message_thread_id"] = int(thread_id)
                except ValueError:
                    pass
            if self.config.reply_to_message and reply_to_message_id is not None:
                kwargs["reply_to_message_id"] = reply_to_message_id
            await bot.send_message(**kwargs)
        for attachment in list(metadata.get("attachments", []) or []):
            if not isinstance(attachment, str):
                continue
            path = Path(attachment)
            if not path.is_file():
                logger.warning("telegram attachment path not found: {}", path)
                continue
            with path.open("rb") as handle:
                kwargs = {"chat_id": chat_id, "document": handle}
                if thread_id:
                    try:
                        kwargs["message_thread_id"] = int(thread_id)
                    except ValueError:
                        pass
                if self.config.reply_to_message and reply_to_message_id is not None:
                    kwargs["reply_to_message_id"] = reply_to_message_id
                await bot.send_document(**kwargs)


def _split_message(text: str, max_len: int) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
