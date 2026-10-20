from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from opc.channels.provider_base import OptionalDependencyChannel
from opc.core.models import SystemMessage


class MatrixChannel(OptionalDependencyChannel):
    name = "matrix"
    required_package = "nio"
    delivery_mode = "polling"

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self.client: Any = None
        self._sync_task: asyncio.Task[Any] | None = None

    def get_required_config_fields(self) -> list[str]:
        return ["homeserver", "access_token", "user_id"]

    def normalize_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        content = payload.get("content", {})
        relates_to = dict(content.get("m.relates_to", {}) or {})
        thread_id = str(relates_to.get("event_id", "") or relates_to.get("m.in_reply_to", {}).get("event_id", "") or "")
        return {
            "sender_id": str(payload.get("sender", "")),
            "chat_id": str(payload.get("room_id", "")),
            "content": str(content.get("body", "") or ""),
            "thread_id": thread_id,
            "reply_to": str(payload.get("event_id", "") or ""),
            "metadata": {
                "matrix": {
                    "event_id": str(payload.get("event_id", "") or ""),
                    "relates_to": relates_to,
                }
            },
        }

    def should_accept_inbound(self, normalized: dict[str, Any]) -> bool:
        sender_id = str(normalized.get("sender_id", "") or "")
        if not sender_id or sender_id == self.config.user_id:
            return False
        return self.is_allowed(sender_id)

    async def start(self) -> None:
        await super().start()
        from nio import AsyncClient, AsyncClientConfig, InviteEvent, RoomMessageText

        store_path = Path(".opc") / "matrix-store"
        store_path.mkdir(parents=True, exist_ok=True)
        self.client = AsyncClient(
            homeserver=self.config.homeserver,
            user=self.config.user_id,
            store_path=str(store_path),
            config=AsyncClientConfig(store_sync_tokens=True, encryption_enabled=self.config.e2ee_enabled),
        )
        self.client.user_id = self.config.user_id
        self.client.access_token = self.config.access_token
        self.client.device_id = self.config.device_id
        self.client.add_event_callback(self._on_text_message, RoomMessageText)
        self.client.add_event_callback(self._on_room_invite, InviteEvent)
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop(self) -> None:
        self.mark_stopped()
        if self.client is not None:
            try:
                self.client.stop_sync_forever()
            except Exception:
                pass
        if self._sync_task is not None:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None
        if self.client is not None:
            try:
                await self.client.close()
            except Exception:
                logger.exception("matrix client close failed")
            self.client = None
        await super().stop()

    async def _sync_loop(self) -> None:
        assert self.client is not None
        try:
            await self.client.sync_forever(timeout=30000, full_state=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.set_last_error(exc)
            raise

    async def _on_room_invite(self, room: Any, event: Any) -> None:
        if self.client is None:
            return
        sender = str(getattr(event, "sender", "") or "")
        if not self.is_allowed(sender):
            return
        try:
            await self.client.join(room.room_id)
        except Exception:
            logger.exception("matrix join failed for {}", room.room_id)

    async def _on_text_message(self, room: Any, event: Any) -> None:
        content = getattr(event, "source", {}).get("content", {})
        payload = {
            "sender": str(getattr(event, "sender", "") or ""),
            "room_id": str(getattr(room, "room_id", "") or ""),
            "event_id": str(getattr(event, "event_id", "") or ""),
            "content": content,
        }
        await self.publish_normalized(self.normalize_event(payload))

    async def send(self, message: SystemMessage) -> None:
        await super().send(message)
        if self.client is None:
            logger.warning("matrix client not connected")
            return
        metadata = dict(message.metadata or {})
        room_id = str(metadata.get("chat_id") or message.session_id or "")
        content: dict[str, Any] = {
            "msgtype": "m.text",
            "body": message.content or "",
        }
        relates_to = self._build_thread_relates_to(metadata)
        if relates_to:
            content["m.relates_to"] = relates_to
        await self.client.room_send(room_id=room_id, message_type="m.room.message", content=content)
        for attachment in list(metadata.get("attachments", []) or []):
            if not isinstance(attachment, str):
                continue
            path = Path(attachment)
            if not path.is_file():
                continue
            with path.open("rb") as handle:
                upload_result = await self.client.upload(
                    handle,
                    content_type="application/octet-stream",
                    filename=path.name,
                    filesize=path.stat().st_size,
                )
            upload_response = upload_result[0] if isinstance(upload_result, tuple) else upload_result
            mxc_url = getattr(upload_response, "content_uri", None)
            if not mxc_url:
                continue
            content = {"msgtype": "m.file", "body": path.name, "filename": path.name, "url": mxc_url}
            if relates_to:
                content["m.relates_to"] = relates_to
            await self.client.room_send(room_id=room_id, message_type="m.room.message", content=content)

    @staticmethod
    def _build_thread_relates_to(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        metadata = dict(metadata or {})
        thread_id = str(metadata.get("thread_id", "") or "")
        reply_to = str(metadata.get("reply_to", "") or "")
        if not thread_id and not reply_to:
            return None
        if thread_id:
            return {"rel_type": "m.thread", "event_id": thread_id}
        return {"m.in_reply_to": {"event_id": reply_to}}
