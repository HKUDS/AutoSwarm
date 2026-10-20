"""Engine lifecycle management for the CLI board."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from opc.core.config import OPCConfig, get_opc_home
from opc.engine import OPCEngine

ProgressCallback = Callable[[str], Awaitable[None] | Awaitable[Any]]
EventCallback = Callable[[Any], Awaitable[None]]


class EngineFacade:
    """Owns a single lazily-initialized OPCEngine for the CLI board process."""

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id
        self._config: OPCConfig | None = None
        self._engine: OPCEngine | None = None
        self._progress_callback: Callable[..., Awaitable[None]] | None = None
        self._event_callback: EventCallback | None = None
        self._init_lock = asyncio.Lock()

    @property
    def engine(self) -> OPCEngine | None:
        return self._engine

    @property
    def store(self):  # noqa: ANN201 - preserve simple property for callers
        return self._engine.store if self._engine else None

    @property
    def opc_home(self) -> Path:
        return get_opc_home()

    def configure_callbacks(
        self,
        *,
        progress_callback: Callable[..., Awaitable[None]] | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        self._progress_callback = progress_callback
        self._event_callback = event_callback
        if self._engine is not None and progress_callback is not None:
            self._engine.on_progress = progress_callback

    async def ensure_ready(self) -> OPCEngine:
        if self._engine is not None:
            return self._engine

        async with self._init_lock:
            if self._engine is not None:
                return self._engine

            config_dir = get_opc_home() / "config"
            self._config = OPCConfig.load(config_dir) if config_dir.exists() else OPCConfig()
            engine = OPCEngine(
                config=self._config,
                project_id=self.project_id,
                on_progress=self._progress_callback,
            )
            await engine.initialize()
            if self._event_callback is not None:
                engine.event_bus.subscribe_all(self._event_callback)
            self._engine = engine
            return engine

    async def shutdown(self) -> None:
        if self._engine is None:
            return
        engine = self._engine
        self._engine = None
        await engine.shutdown()

