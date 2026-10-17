"""Shared provider helpers for native AutoSwarm channels."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from opc.channels.base import BaseChannel
from opc.core.models import SystemMessage


class OptionalDependencyChannel(BaseChannel):
    required_package: str | None = None
    delivery_mode: str = "sdk"
    reconnect_delay_seconds: float = 5.0

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self._runner_task: asyncio.Task[Any] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @classmethod
    def is_available(cls) -> bool:
        if not cls.required_package:
            return True
        try:
            __import__(cls.required_package)
            return True
        except Exception:
            return False

    def dependency_error(self) -> str:
        return f"{self.name} channel requires optional dependency `{self.required_package}`"

    def get_required_config_fields(self) -> list[str]:
        return []

    def config_error(self) -> str:
        missing = self.get_missing_config_fields()
        return f"{self.name} channel is missing required config fields: {', '.join(missing)}"

    def is_ready(self) -> bool:
        return self.is_available() and self.is_configured()

    def describe_capability(self) -> dict[str, Any]:
        data = super().describe_capability()
        data.update(
            {
                "delivery_mode": self.delivery_mode,
                "available": self.is_available(),
                "ready": self.is_ready(),
            }
        )
        return data

    def build_outbound_envelope(self, message: SystemMessage) -> dict[str, Any]:
        metadata = dict(message.metadata or {})
        return {
            "channel": self.name,
            "chat_id": str(metadata.get("chat_id") or message.session_id),
            "thread_id": str(metadata.get("thread_id") or ""),
            "reply_to": str(metadata.get("reply_to") or ""),
            "content": message.content,
            "attachments": list(metadata.get("attachments", []) or []),
            "message_type": message.message_type,
            "metadata": metadata,
        }

    async def start(self) -> None:
        if not self.is_available():
            raise RuntimeError(self.dependency_error())
        if not self.is_configured():
            raise RuntimeError(self.config_error())
        self.mark_started()
        logger.info("{} channel started", self.name)

    async def stop(self) -> None:
        self.mark_stopped()
        if self._runner_task:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except asyncio.CancelledError:
                pass
            self._runner_task = None
        if self._background_tasks:
            for task in list(self._background_tasks):
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        logger.info("{} channel stopped", self.name)

    async def send(self, message: SystemMessage) -> None:
        self.last_outbound = self.build_outbound_envelope(message)
        logger.info("{} outbound -> {} :: {}", self.name, self.last_outbound["chat_id"], message.content[:120])

    def _track_task(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _run_with_restarts(self, callback: Any, *, label: str, delay_seconds: float | None = None) -> None:
        delay = self.reconnect_delay_seconds if delay_seconds is None else max(0.1, delay_seconds)
        while self.is_running:
            try:
                await callback()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.set_last_error(exc)
                logger.warning("{} {} error: {}", self.name, label, exc)
                if not self.is_running:
                    return
                await asyncio.sleep(delay)


class WebhookChannel(OptionalDependencyChannel):
    required_package = None
    delivery_mode = "webhook"

    async def handle_webhook(self, payload: dict[str, Any]) -> None:
        await self.publish_normalized(payload)


class PollingChannel(OptionalDependencyChannel):
    delivery_mode = "polling"

    async def start(self) -> None:
        await super().start()
        self._runner_task = asyncio.create_task(self._run_with_restarts(self._polling_loop, label="polling"))

    async def _polling_loop(self) -> None:
        while self.is_running:
            await self.poll_once()
            await asyncio.sleep(max(0.01, self.get_poll_interval_seconds()))

    def get_poll_interval_seconds(self) -> float:
        return 1.0

    async def poll_once(self) -> None:
        raise NotImplementedError


class SocketChannel(OptionalDependencyChannel):
    delivery_mode = "socket"

    async def start(self) -> None:
        await super().start()
        self._runner_task = asyncio.create_task(self._run_with_restarts(self.run_socket_forever, label="socket"))

    async def run_socket_forever(self) -> None:
        raise NotImplementedError
