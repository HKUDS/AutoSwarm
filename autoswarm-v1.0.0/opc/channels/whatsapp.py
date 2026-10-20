from __future__ import annotations

import json
import mimetypes
from collections import OrderedDict
from typing import Any

from loguru import logger

from opc.channels.provider_base import SocketChannel
from opc.core.models import SystemMessage


class WhatsAppChannel(SocketChannel):
    name = "whatsapp"
    required_package = "websockets"
    delivery_mode = "bridge"

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self._ws: Any = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()

    def get_required_config_fields(self) -> list[str]:
        return ["bridge_url"]

    def normalize_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = payload.get("message", payload)
        sender = str(message.get("sender", message.get("from", "")) or "")
        if not sender:
            sender = str(payload.get("sender", payload.get("from", "")) or "")
        pn = str(message.get("pn", "") or "")
        if not pn:
            pn = str(payload.get("pn", "") or "")
        sender_id = pn or sender
        if "@" in sender_id:
            sender_id = sender_id.split("@", 1)[0]
        content = str(message.get("text", message.get("content", message.get("body", ""))) or "")
        attachments = list(message.get("media", []) or [])
        if attachments:
            for path in attachments:
                mime, _ = mimetypes.guess_type(path)
                tag = "image" if mime and mime.startswith("image/") else "file"
                content = f"{content}\n[{tag}: {path}]".strip() if content else f"[{tag}: {path}]"
        return {
            "sender_id": sender_id,
            "chat_id": str(message.get("chat_id", sender) or ""),
            "content": content,
            "thread_id": "",
            "reply_to": str(message.get("reply_to", message.get("id", "")) or ""),
            "attachments": attachments,
            "metadata": {
                "message_id": str(message.get("id", "") or ""),
                "is_group": bool(message.get("isGroup", False)),
                "raw_sender": sender,
            },
        }

    async def run_socket_forever(self) -> None:
        import websockets

        async with websockets.connect(self.config.bridge_url) as ws:
            self._ws = ws
            if self.config.bridge_token:
                await ws.send(json.dumps({"type": "auth", "token": self.config.bridge_token}))
            async for raw in ws:
                await self._handle_bridge_message(raw)

    async def stop(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.exception("whatsapp bridge close failed")
            self._ws = None
        await super().stop()

    async def send(self, message: SystemMessage) -> None:
        await super().send(message)
        if self._ws is None:
            logger.warning("whatsapp bridge not connected")
            return
        metadata = dict(message.metadata or {})
        payload = {
            "type": "send",
            "to": str(metadata.get("chat_id") or message.session_id or ""),
            "text": message.content,
            "attachments": list(metadata.get("attachments", []) or []),
        }
        await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def _handle_bridge_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("invalid whatsapp bridge payload: {}", raw[:100])
            return
        msg_type = str(payload.get("type", "") or "")
        if msg_type != "message":
            if msg_type == "error":
                self.set_last_error(str(payload.get("error", "bridge error")))
            return
        message_id = str(payload.get("id", "") or payload.get("message", {}).get("id", "") or "")
        if message_id:
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)
        normalized = self.normalize_event(payload)
        await self.publish_normalized(normalized)
