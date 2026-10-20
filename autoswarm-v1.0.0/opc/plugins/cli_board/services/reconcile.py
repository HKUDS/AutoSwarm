"""Periodic reconcile loop for cross-process board consistency."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class ReconcileLoop:
    """Run a callback periodically until stopped."""

    def __init__(self, interval_seconds: float, callback: Callable[[], Awaitable[None]]) -> None:
        self.interval_seconds = max(0.5, float(interval_seconds))
        self._callback = callback
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            await self._callback()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop_event.set()

