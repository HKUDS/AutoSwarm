from __future__ import annotations

from collections import deque
from typing import Any

from loguru import logger

from opc.channels.provider_base import OptionalDependencyChannel
from opc.core.models import SystemMessage


class QQChannel(OptionalDependencyChannel):
    name = "qq"
    required_package = "botpy"
    delivery_mode = "socket"

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self._client: Any = None
        self._processed_ids: deque[str] = deque(maxlen=1000)
        self._msg_seq: int = 1

    def get_required_config_fields(self) -> list[str]:
        return ["app_id", "secret"]

    def normalize_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        author = payload.get("author", {})
        sender = str(author.get("id", payload.get("openid", "")))
        return {
            "sender_id": sender,
            "chat_id": str(payload.get("group_openid", payload.get("channel_id", sender))),
            "content": str(payload.get("content", "") or ""),
            "thread_id": "",
            "reply_to": str(payload.get("id", "") or ""),
            "metadata": {"message_id": str(payload.get("id", "") or "")},
        }

    async def start(self) -> None:
        await super().start()
        import botpy

        self._client = self._build_client(botpy)
        await self._client.start(appid=self.config.app_id, secret=self.config.secret)

    def _build_client(self, botpy_module: Any) -> Any:
        intents = botpy_module.Intents(public_messages=True, direct_message=True)
        channel = self

        class OPCQQClient(botpy_module.Client):
            def __init__(self) -> None:
                super().__init__(intents=intents, ext_handlers=False)

            async def on_ready(self) -> None:
                logger.info("qq bot ready")

            async def on_c2c_message_create(self, message: Any) -> None:
                await channel._on_message(message)

            async def on_direct_message_create(self, message: Any) -> None:
                await channel._on_message(message)

        return OPCQQClient()

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.exception("qq client close failed")
            self._client = None
        await super().stop()

    async def _on_message(self, message: Any) -> None:
        if getattr(message, "id", "") in self._processed_ids:
            return
        self._processed_ids.append(getattr(message, "id", ""))
        author = getattr(message, "author", None)
        payload = {
            "id": str(getattr(message, "id", "") or ""),
            "author": {
                "id": str(getattr(author, "id", "") or getattr(author, "user_openid", "") or ""),
            },
            "content": str(getattr(message, "content", "") or ""),
        }
        await self.publish_normalized(self.normalize_event(payload))

    async def send(self, message: SystemMessage) -> None:
        await super().send(message)
        if self._client is None:
            logger.warning("qq client not connected")
            return
        metadata = dict(message.metadata or {})
        chat_id = str(metadata.get("chat_id") or message.session_id or "")
        self._msg_seq += 1
        await self._client.api.post_c2c_message(
            openid=chat_id,
            msg_type=0,
            content=message.content,
            msg_id=str(metadata.get("reply_to", "") or metadata.get("message_id", "") or ""),
            msg_seq=self._msg_seq,
        )
