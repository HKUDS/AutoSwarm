"""Base channel interface for external messaging platforms."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from loguru import logger

from opc.channels.session import ChannelSessionMapping
from opc.core.models import SystemMessage, UserMessage
from opc.layer0_interaction.message_bus import MessageBus


class BaseChannel(ABC):
    name: str = "base"
    dependency_name: str | None = None

    def __init__(self, config: Any, bus: MessageBus):
        self.config = config
        self.bus = bus
        self._running = False
        self._started_at: datetime | None = None
        self._last_error: str = ""
        self._status_reason: str = ""
        self.last_outbound: dict[str, Any] | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_error(self) -> str:
        return self._last_error

    def set_status_reason(self, reason: str = "") -> None:
        self._status_reason = reason.strip()

    def set_last_error(self, error: Exception | str | None) -> None:
        self._last_error = str(error or "").strip()
        if self._last_error:
            logger.warning("{} runtime error: {}", self.name, self._last_error)
        else:
            logger.debug("{} runtime error cleared", self.name)

    def mark_started(self) -> None:
        self._running = True
        self._started_at = datetime.now()
        self.set_last_error("")

    def mark_stopped(self) -> None:
        self._running = False

    def get_required_config_fields(self) -> list[str]:
        return []

    def get_missing_config_fields(self) -> list[str]:
        missing: list[str] = []
        for field_name in self.get_required_config_fields():
            value = getattr(self.config, field_name, None)
            if value is None:
                missing.append(field_name)
            elif isinstance(value, str) and not value.strip():
                missing.append(field_name)
            elif isinstance(value, list) and not value:
                missing.append(field_name)
        return missing

    def is_configured(self) -> bool:
        return not self.get_missing_config_fields()

    def is_allowed(self, sender_id: str) -> bool:
        allow_list = getattr(self.config, "allow_from", [])
        if not allow_list:
            logger.warning("{}: allow_from is empty — all access denied", self.name)
            return False
        if "*" in allow_list:
            return True
        sender = str(sender_id)
        return sender in allow_list or any(part in allow_list for part in sender.split("|") if part)

    def describe_capability(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dependency": self.dependency_name,
            "running": self.is_running,
            "configured": self.is_configured(),
            "missing_config": self.get_missing_config_fields(),
            "last_error": self.last_error,
            "status_reason": self._status_reason,
            "started_at": self._started_at.isoformat() if self._started_at else "",
        }

    def should_accept_inbound(self, normalized: dict[str, Any]) -> bool:
        sender_id = str(normalized.get("sender_id", "") or "")
        return bool(sender_id) and self.is_allowed(sender_id)

    def build_session_key_override(self, normalized: dict[str, Any]) -> str | None:
        _ = normalized
        return None

    def build_inbound_metadata(self, normalized: dict[str, Any], attachments: list[Any]) -> dict[str, Any]:
        metadata = dict(normalized.get("metadata", {}) or {})
        metadata.setdefault("chat_id", str(normalized.get("chat_id", "") or ""))
        metadata.setdefault("sender_id", str(normalized.get("sender_id", "") or ""))
        metadata.setdefault("reply_to", str(normalized.get("reply_to", "") or ""))
        metadata.setdefault("thread_id", str(normalized.get("thread_id", "") or ""))
        metadata["attachments"] = list(attachments or [])
        return metadata

    def map_session(
        self,
        *,
        chat_id: str,
        thread_id: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        route = ChannelSessionMapping.derive(
            channel=self.name,
            chat_id=chat_id,
            thread_id=thread_id,
            reply_to=reply_to,
            metadata=metadata,
        )
        return {
            "session_id": route.session_id,
            "chat_id": route.chat_id,
            "thread_id": route.thread_id,
            "reply_to": route.reply_to,
        }

    async def publish_inbound(
        self,
        *,
        sender_id: str,
        chat_id: str,
        content: str,
        attachments: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        if not self.is_allowed(sender_id):
            logger.warning("Access denied for sender {} on channel {}", sender_id, self.name)
            return
        meta = dict(metadata or {})
        route = self.map_session(
            chat_id=chat_id,
            thread_id=str(meta.get("thread_id", "") or ""),
            reply_to=str(meta.get("reply_to", "") or ""),
            metadata=meta,
        )
        await self.bus.publish_inbound(
            UserMessage(
                channel=self.name,
                user_id=str(sender_id),
                content=content,
                attachments=attachments or [],
                session_id=session_key or route["session_id"],
                metadata={
                    "chat_id": route["chat_id"],
                    "sender_id": str(sender_id),
                    "reply_to": route["reply_to"],
                    "thread_id": route["thread_id"],
                    "attachments": list(attachments or []),
                    **meta,
                },
            )
        )

    async def publish_normalized(self, normalized: dict[str, Any]) -> bool:
        sender_id = str(normalized.get("sender_id", "") or "")
        chat_id = str(normalized.get("chat_id", "") or "")
        content = str(normalized.get("content", "") or "")
        attachments = list(normalized.get("attachments", []) or [])
        if not sender_id or not chat_id:
            logger.debug("{} inbound payload missing sender/chat identifiers: {}", self.name, normalized)
            return False
        if not self.should_accept_inbound(normalized):
            logger.debug("{} inbound payload rejected by policy: {}", self.name, normalized)
            return False
        await self.publish_inbound(
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            attachments=attachments,
            metadata=self.build_inbound_metadata(normalized, attachments),
            session_key=self.build_session_key_override(normalized),
        )
        return True

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send(self, message: SystemMessage) -> None:
        raise NotImplementedError
