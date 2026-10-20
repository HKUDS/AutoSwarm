from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from opc.channels.provider_base import SocketChannel
from opc.core.models import SystemMessage


class SlackChannel(SocketChannel):
    name = "slack"
    required_package = "slack_sdk"

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self._web_client: Any = None
        self._socket_client: Any = None
        self._bot_user_id: str | None = None

    def get_required_config_fields(self) -> list[str]:
        return ["bot_token", "app_token"]

    def normalize_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = payload.get("event", payload)
        text = str(event.get("text", "") or "")
        channel_type = str(event.get("channel_type", "") or "")
        thread_ts = str(event.get("thread_ts", "") or "")
        if self.config.reply_in_thread and channel_type != "im" and not thread_ts:
            thread_ts = str(event.get("ts", "") or "")
        return {
            "sender_id": str(event.get("user", "")),
            "chat_id": str(event.get("channel", "")),
            "content": self._strip_bot_mention(text),
            "thread_id": thread_ts if channel_type != "im" else "",
            "reply_to": str(event.get("ts", "") or ""),
            "metadata": {
                "slack": {
                    "thread_ts": thread_ts,
                    "channel_type": channel_type,
                    "event": event,
                }
            },
        }

    def should_accept_inbound(self, normalized: dict[str, Any]) -> bool:
        sender_id = str(normalized.get("sender_id", "") or "")
        if not sender_id or sender_id == self._bot_user_id:
            return False
        meta = dict(normalized.get("metadata", {}) or {})
        slack_meta = dict(meta.get("slack", {}) or {})
        channel_type = str(slack_meta.get("channel_type", "") or "")
        chat_id = str(normalized.get("chat_id", "") or "")
        if channel_type == "im":
            if not self.config.dm.enabled:
                return False
            if self.config.dm.policy == "allowlist":
                return sender_id in list(self.config.dm.allow_from or [])
            return self.is_allowed(sender_id)
        if self.config.group_policy == "allowlist":
            return chat_id in list(self.config.group_allow_from or [])
        if self.config.group_policy == "mention":
            text = str(normalized.get("content", "") or "")
            raw_text = str(slack_meta.get("event", {}).get("text", "") or "")
            if text != raw_text:
                return True
            return False
        return self.is_allowed(sender_id)

    def build_session_key_override(self, normalized: dict[str, Any]) -> str | None:
        meta = dict(normalized.get("metadata", {}) or {})
        slack_meta = dict(meta.get("slack", {}) or {})
        channel_type = str(slack_meta.get("channel_type", "") or "")
        thread_ts = str(slack_meta.get("thread_ts", "") or "")
        if thread_ts and channel_type != "im":
            return f"slack:{normalized.get('chat_id', '')}:{thread_ts}"
        return None

    async def run_socket_forever(self) -> None:
        if self.config.mode != "socket":
            raise RuntimeError(f"unsupported slack mode: {self.config.mode}")
        from slack_sdk.socket_mode.websockets import SocketModeClient
        from slack_sdk.web.async_client import AsyncWebClient

        self._web_client = self._create_web_client()
        auth = await self._web_client.auth_test()
        self._bot_user_id = auth.get("user_id")
        self._socket_client = self._create_socket_client(self._web_client)
        self._socket_client.socket_mode_request_listeners.append(self._on_socket_request)
        await self._socket_client.connect()
        while self.is_running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self.mark_stopped()
        if self._socket_client is not None:
            try:
                await self._socket_client.close()
            except Exception:
                logger.exception("slack socket close failed")
            self._socket_client = None
        await super().stop()

    def _create_web_client(self) -> Any:
        from slack_sdk.web.async_client import AsyncWebClient

        return AsyncWebClient(token=self.config.bot_token)

    def _create_socket_client(self, web_client: Any) -> Any:
        from slack_sdk.socket_mode.websockets import SocketModeClient

        return SocketModeClient(app_token=self.config.app_token, web_client=web_client)

    async def send(self, message: SystemMessage) -> None:
        await super().send(message)
        if self._web_client is None:
            logger.warning("slack client not connected")
            return
        metadata = dict(message.metadata or {})
        chat_id = str(metadata.get("chat_id") or message.session_id or "")
        slack_meta = dict(metadata.get("slack", {}) or {})
        thread_ts = str(metadata.get("thread_id") or slack_meta.get("thread_ts") or "")
        channel_type = str(slack_meta.get("channel_type", "") or "")
        use_thread = bool(thread_ts and channel_type != "im" and self.config.reply_in_thread)
        if message.content.strip():
            await self._web_client.chat_postMessage(
                channel=chat_id,
                text=message.content,
                thread_ts=thread_ts if use_thread else None,
            )
        for attachment in list(metadata.get("attachments", []) or []):
            if isinstance(attachment, str):
                await self._web_client.files_upload_v2(
                    channel=chat_id,
                    file=attachment,
                    thread_ts=thread_ts if use_thread else None,
                )

    async def _on_socket_request(self, client: Any, req: Any) -> None:
        try:
            from slack_sdk.socket_mode.response import SocketModeResponse
        except Exception:
            class SocketModeResponse:  # type: ignore[no-redef]
                def __init__(self, envelope_id: str):
                    self.envelope_id = envelope_id

        if getattr(req, "type", "") != "events_api":
            return
        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        payload = req.payload or {}
        event = payload.get("event") or {}
        event_type = str(event.get("type", "") or "")
        if event_type not in {"message", "app_mention"}:
            return
        if event.get("subtype"):
            return
        normalized = self.normalize_event(payload)
        normalized["metadata"]["slack"]["event_type"] = event_type
        normalized["content"] = self._strip_bot_mention(str(event.get("text", "") or ""))
        if not normalized["sender_id"] or not normalized["chat_id"]:
            return
        if self._web_client and event.get("ts") and self.config.react_emoji:
            try:
                await self._web_client.reactions_add(
                    channel=normalized["chat_id"],
                    name=self.config.react_emoji,
                    timestamp=event.get("ts"),
                )
            except Exception:
                logger.debug("slack reactions_add failed")
        await self.publish_normalized(normalized)

    def _strip_bot_mention(self, text: str) -> str:
        if not text or not self._bot_user_id:
            return text
        return re.sub(rf"<@{re.escape(self._bot_user_id)}>\s*", "", text).strip()
