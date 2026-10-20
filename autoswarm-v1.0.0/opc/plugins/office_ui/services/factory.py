"""Factory for shared Office services."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Awaitable, Callable

import aiosqlite

from opc.core.config import OPCConfig, get_opc_home
from opc.engine import OPCEngine
from opc.plugins.office_ui.agent_store import AgentStore
from opc.plugins.office_ui.chat_store import ChatStore
from opc.plugins.office_ui.event_adapter import EventAdapter

from . import OfficeServices
from .context import ModeState, OfficeServiceContext


class OfficeServiceFactory:
    """Async context manager that owns engine and UI-state persistence."""

    def __init__(
        self,
        *,
        config: OPCConfig | None = None,
        project_id: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_runtime_event: Callable[[Any], Awaitable[None]] | None = None,
        on_escalation: Callable[..., Awaitable[str | None]] | None = None,
    ) -> None:
        self.config = config
        self.project_id = project_id
        self.on_progress = on_progress
        self.on_runtime_event = on_runtime_event
        self.on_escalation = on_escalation
        self.db: aiosqlite.Connection | None = None
        self.engine: OPCEngine | None = None
        self.services: OfficeServices | None = None

    async def __aenter__(self) -> OfficeServices:
        if self.config is None:
            config_dir = get_opc_home() / "config"
            self.config = OPCConfig.load(config_dir) if config_dir.exists() else OPCConfig()
        self.engine = OPCEngine(
            config=self.config,
            project_id=self.project_id,
            on_progress=self.on_progress,
            on_runtime_event=self.on_runtime_event,
            on_escalation=self.on_escalation,
        )
        self.db = await aiosqlite.connect(str(self.engine.opc_home / "ui_state.db"))
        # Wait for a concurrent writer (e.g. a running office-UI server)
        # instead of failing after sqlite's 5s default with 'database is locked'.
        await self.db.execute("PRAGMA busy_timeout=30000")
        agent_store = AgentStore(self.db)
        await agent_store.initialize()
        chat_store = ChatStore(self.db)
        await chat_store.initialize()
        event_adapter = EventAdapter()
        await self.engine.initialize()
        mode_state = ModeState(
            exec_mode=await agent_store.get_server_state("exec_mode", "task"),
            company_profile=await agent_store.get_server_state("company_profile", "corporate"),
            task_preferred_agent=await agent_store.get_server_state("task_preferred_agent", "native"),
        )
        context = OfficeServiceContext(
            engine=self.engine,
            agent_store=agent_store,
            chat_store=chat_store,
            event_adapter=event_adapter,
            mode_state=mode_state,
        )
        self.services = OfficeServices(context)
        return self.services

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.engine is not None:
            await self.engine.shutdown()
        if self.db is not None:
            await self.db.close()
