"""Coalesce contiguous text deltas without delaying execution boundaries."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


class RuntimeDeltaBuffer:
    def __init__(self, emit: Callable[[dict[str, Any]], Awaitable[None]], *, delay_ms: int, max_chars: int) -> None:
        self.emit = emit
        self.delay = delay_ms / 1000
        self.max_chars = max_chars
        self._pending: dict[str, Any] | None = None
        self._timer: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._failure: Exception | None = None
        self._closed = False

    @staticmethod
    def _key(payload: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(payload.get(key) for key in ("runtime_session_id", "task_id", "type", "stream_id"))

    async def push(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            if self._failure is not None:
                raise self._failure
            delta = payload.get("type") in {"assistant_delta", "thinking_delta"} and payload.get("stream_id")
            if self._pending and (not delta or self._key(self._pending) != self._key(payload)):
                await self._flush_locked()
            if not delta or self.delay <= 0 or self._closed:
                await self.emit(payload)
                return
            if self._pending is None:
                self._pending = dict(payload)
                self._timer = asyncio.create_task(self._expire())
            else:
                text = str(self._pending.get("text", "")) + str(payload.get("text", ""))
                timestamp = self._pending.get("timestamp_ms")
                self._pending = {**payload, "text": text, "timestamp_ms": timestamp}
            if len(str(self._pending.get("text", ""))) >= self.max_chars:
                await self._flush_locked()

    async def _flush_locked(self) -> None:
        if self._timer is not None:
            timer, self._timer = self._timer, None
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        if self._pending is not None:
            payload, self._pending = self._pending, None
            await self.emit(payload)

    async def _expire(self) -> None:
        try:
            await asyncio.sleep(self.delay)
            async with self._lock:
                self._timer = None
                await self._flush_locked()
        except Exception as exc:
            self._failure = exc

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            await self._flush_locked()
            if self._failure is not None:
                raise self._failure
