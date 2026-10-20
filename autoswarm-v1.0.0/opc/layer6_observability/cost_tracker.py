"""Cost tracking for LLM API usage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from opc.database.store import OPCStore
from opc.core.events import EventBus
from opc.core.models import OPCEvent, CostEvent


@dataclass
class CostEntry:
    task_id: str | None = None
    agent_id: str | None = None
    org_id: str | None = None
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


class CostTracker:
    """Tracks LLM API costs per task and agent."""

    def __init__(self, store: OPCStore, event_bus: EventBus | None = None) -> None:
        self.store = store
        self.event_bus = event_bus
        self._session_total = 0.0

    async def record(self, entry: CostEntry) -> None:
        await self.store.record_cost(
            task_id=entry.task_id,
            agent_id=entry.agent_id,
            model=entry.model,
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
            cost=entry.cost,
        )
        # Also record CostEvent for cost_events table (org-scoped tracking)
        event = CostEvent(
            org_id=entry.org_id,
            agent_id=entry.agent_id,
            task_id=entry.task_id,
            model=entry.model,
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
            cost_usd=entry.cost,
            timestamp=entry.timestamp,
        )
        await self.store.record_cost_event(event)
        self._session_total += entry.cost

        if self.event_bus:
            await self.event_bus.publish(OPCEvent(
                event_type="cost_update",
                payload={
                    "task_id": entry.task_id,
                    "cost": entry.cost,
                    "session_total": self._session_total,
                },
            ))

    async def check_budget(
        self,
        agent_id: str | None = None,
        org_id: str | None = None,
    ) -> tuple[bool, str]:
        """Check if agent/org is within budget. Returns (allowed, reason)."""
        return await check_budget(self.store, agent_id=agent_id, org_id=org_id)

    async def get_summary(self, project_id: str | None = None) -> dict[str, Any]:
        db_totals = await self.store.get_total_cost(project_id)
        return {
            **db_totals,
            "session_cost": self._session_total,
        }

    @property
    def session_total(self) -> float:
        return self._session_total


async def check_budget(
    store: OPCStore,
    agent_id: str | None = None,
    org_id: str | None = None,
) -> tuple[bool, str]:
    """Check if agent/org is within budget. Returns (allowed, reason)."""
    if org_id:
        org = await store.get_organization(org_id)
        if org and org.budget_monthly_cents > 0:
            if org.spent_monthly_cents >= org.budget_monthly_cents:
                return False, f"Organization '{org.name}' has exceeded its monthly budget"
    if agent_id and org_id:
        agents = await store.list_org_agents(org_id)
        for agent in agents:
            if agent.agent_id == agent_id or agent.role_id == agent_id:
                if agent.budget_monthly_cents > 0 and agent.spent_monthly_cents >= agent.budget_monthly_cents:
                    return False, f"Agent '{agent.name}' has exceeded its monthly budget"
                break
    return True, ""
