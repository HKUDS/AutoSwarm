"""Shared Office services used by WebSocket UI, CLI, and CLI board."""

from __future__ import annotations

from .agent import AgentService
from .comms import CommsService
from .context import ModeState, OfficeServiceContext
from .kanban import KanbanService
from .market import MarketService
from .models import ServiceError, ServiceEvent, ServiceResult
from .org import OrgService
from .project import ProjectService
from .runtime import RuntimeService
from .session import SessionService
from .talent import TalentService
from .work_item import WorkItemService


class OfficeServices:
    def __init__(self, context: OfficeServiceContext) -> None:
        self.context = context
        self.project = ProjectService(context)
        self.session = SessionService(context)
        self.kanban = KanbanService(context, self.session)
        self.runtime = RuntimeService(context, self.session)
        self.agent = AgentService(context)
        self.org = OrgService(context)
        self.talent = TalentService(context)
        self.market = MarketService(context)
        self.comms = CommsService(context)
        self.work_item = WorkItemService(context)


__all__ = [
    "AgentService",
    "CommsService",
    "KanbanService",
    "MarketService",
    "ModeState",
    "OfficeServiceContext",
    "OfficeServices",
    "OrgService",
    "ProjectService",
    "RuntimeService",
    "ServiceError",
    "ServiceEvent",
    "ServiceResult",
    "SessionService",
    "TalentService",
    "WorkItemService",
]
