"""In-process event bus for AutoSwarm system."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

from opc.core.models import OPCEvent


Listener = Callable[[OPCEvent], Coroutine[Any, Any, None]]


class EventBus:
    """Simple async pub/sub event bus for inter-layer communication."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._global_listeners: list[Listener] = []
        self._history: list[OPCEvent] = []
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def subscribe(self, event_type: str, listener: Listener) -> None:
        self._listeners[event_type].append(listener)

    def subscribe_all(self, listener: Listener) -> None:
        self._global_listeners.append(listener)

    async def publish(self, event: OPCEvent) -> None:
        async with self._get_lock():
            self._history.append(event)
            # Snapshot listener lists under lock to avoid mutation during iteration
            typed = list(self._listeners.get(event.event_type, []))
            globl = list(self._global_listeners)
        # Execute listeners outside lock to avoid holding it during async work
        tasks = [fn(event) for fn in typed + globl]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_history(self, event_type: str | None = None, limit: int = 50) -> list[OPCEvent]:
        events = self._history
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events[-limit:]
