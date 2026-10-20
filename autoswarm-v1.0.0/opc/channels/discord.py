from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from opc.channels.provider_base import SocketChannel
from opc.core.models import SystemMessage


class DiscordChannel(SocketChannel):
    name = "discord"
    required_package = "discord"

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self._client: Any = None
        self._bot_user_id: str | None = None

    def get_required_config_fields(self) -> list[str]:
        return ["token"]

    def normalize_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        author = payload.get("author", {})
        channel_id = str(payload.get("channel_id", "") or "")
        thread_id = ""
        if payload.get("is_thread"):
            thread_id = str(payload.get("thread_id", channel_id) or "")
        attachments = []
        for item in list(payload.get("attachments", []) or []):
            attachments.append(
                {
                    "url": str(item.get("url", "") or ""),
                    "filename": str(item.get("filename", "") or ""),
                    "content_type": str(item.get("content_type", "") or ""),
                }
            )
        return {
            "sender_id": str(author.get("id", "")),
            "chat_id": channel_id,
            "content": str(payload.get("content", "") or ""),
            "thread_id": thread_id,
            "reply_to": str((payload.get("message_reference") or {}).get("message_id", "") or ""),
            "attachments": attachments,
            "metadata": {
                "channel_type": str(payload.get("channel_type", "") or ""),
                "guild_id": str(payload.get("guild_id", "") or ""),
                "message_id": str(payload.get("id", "") or ""),
                "mentions_bot": bool(payload.get("mentions_bot", False)),
            },
        }

    def should_accept_inbound(self, normalized: dict[str, Any]) -> bool:
        sender_id = str(normalized.get("sender_id", "") or "")
        if not sender_id or sender_id == self._bot_user_id:
            return False
        metadata = dict(normalized.get("metadata", {}) or {})
        channel_type = str(metadata.get("channel_type", "") or "")
        if channel_type == "dm":
            return self.is_allowed(sender_id)
        if self.config.group_policy == "open":
            return self.is_allowed(sender_id)
        if self.config.group_policy == "allowlist":
            return str(normalized.get("chat_id", "") or "") in list(getattr(self.config, "group_allow_from", []) or [])
        return bool(metadata.get("mentions_bot"))

    async def run_socket_forever(self) -> None:
        import discord

        self._client = self._build_client(discord)
        await self._client.start(self.config.token)

    def _build_client(self, discord_module: Any) -> Any:
        intents = discord_module.Intents.default()
        intents.message_content = True
        intents.guild_messages = True
        intents.dm_messages = True
        intents.guilds = True
        channel = self

        class OPCDiscordClient(discord_module.Client):
            async def on_ready(self) -> None:
                channel._bot_user_id = str(self.user.id) if self.user else None
                logger.info("discord bot connected as {}", self.user)

            async def on_message(self, message: Any) -> None:
                if not message or not getattr(message, "author", None):
                    return
                payload = {
                    "id": str(message.id),
                    "author": {"id": str(message.author.id)},
                    "channel_id": str(message.channel.id),
                    "guild_id": str(getattr(message.guild, "id", "") or ""),
                    "content": str(message.content or ""),
                    "is_thread": bool(getattr(message.channel, "thread", None) or getattr(message.channel, "parent", None)),
                    "thread_id": str(getattr(message.channel, "id", "") if isinstance(message.channel, discord_module.Thread) else ""),
                    "channel_type": "dm" if isinstance(message.channel, discord_module.DMChannel) else "guild",
                    "mentions_bot": bool(self.user and self.user in getattr(message, "mentions", [])),
                    "attachments": [
                        {
                            "url": str(att.url),
                            "filename": str(att.filename),
                            "content_type": str(att.content_type or ""),
                        }
                        for att in list(getattr(message, "attachments", []) or [])
                    ],
                }
                if getattr(message, "reference", None) and getattr(message.reference, "message_id", None):
                    payload["message_reference"] = {"message_id": str(message.reference.message_id)}
                await channel.publish_normalized(channel.normalize_event(payload))

        return OPCDiscordClient(intents=intents)

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.exception("discord client close failed")
            self._client = None
        await super().stop()

    async def send(self, message: SystemMessage) -> None:
        await super().send(message)
        if self._client is None:
            logger.warning("discord client not connected")
            return
        metadata = dict(message.metadata or {})
        channel_id = int(str(metadata.get("chat_id") or message.session_id))
        channel = self._client.get_channel(channel_id) or await self._client.fetch_channel(channel_id)
        reference = None
        reply_to = str(metadata.get("reply_to", "") or "")
        if reply_to:
            try:
                reference = await channel.fetch_message(int(reply_to))
            except Exception:
                reference = None
        files = []
        for attachment in list(metadata.get("attachments", []) or []):
            if isinstance(attachment, str) and Path(attachment).is_file():
                import discord

                files.append(discord.File(attachment))
        if files:
            await channel.send(content=message.content or None, files=files, reference=reference)
        elif message.content.strip():
            await channel.send(content=message.content, reference=reference)
