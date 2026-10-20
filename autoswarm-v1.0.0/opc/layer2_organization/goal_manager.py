"""Goal hierarchy manager for persistent company-mode organizations."""

from __future__ import annotations

from typing import Any

from loguru import logger

from opc.core.models import Goal, GoalLevel, GoalStatus
from opc.database.store import OPCStore


class GoalManager:
    """Manages hierarchical goals within an organization."""

    def __init__(self, store: OPCStore) -> None:
        self.store = store

    async def create_goal(
        self,
        org_id: str,
        title: str,
        description: str = "",
        level: GoalLevel = GoalLevel.TASK,
        parent_id: str | None = None,
        owner_agent_id: str | None = None,
        priority: int = 5,
        metadata: dict[str, Any] | None = None,
    ) -> Goal:
        goal = Goal(
            org_id=org_id,
            parent_id=parent_id,
            owner_agent_id=owner_agent_id,
            level=level,
            title=title,
            description=description,
            priority=priority,
            metadata=dict(metadata or {}),
        )
        await self.store.save_goal(goal)
        logger.info("Created goal {} ({}) in org {}", goal.goal_id, title, org_id)
        return goal

    async def get_goal(self, goal_id: str) -> Goal | None:
        return await self.store.get_goal(goal_id)

    async def get_goal_tree(self, org_id: str) -> list[dict[str, Any]]:
        """Return hierarchical goal tree for an organization."""
        all_goals = await self.store.get_goal_tree(org_id)
        by_parent: dict[str | None, list[Goal]] = {}
        for goal in all_goals:
            by_parent.setdefault(goal.parent_id, []).append(goal)

        def _build(parent_id: str | None) -> list[dict[str, Any]]:
            children = by_parent.get(parent_id, [])
            return [
                {
                    "goal": goal,
                    "children": _build(goal.goal_id),
                }
                for goal in children
            ]

        return _build(None)

    async def get_goal_chain(self, goal_id: str) -> list[Goal]:
        """Walk from a goal up to the root, returning [leaf, ..., root]."""
        chain: list[Goal] = []
        current_id: str | None = goal_id
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            goal = await self.store.get_goal(current_id)
            if not goal:
                break
            chain.append(goal)
            current_id = goal.parent_id
        return chain

    async def link_task_to_goal(self, task_id: str, goal_id: str) -> None:
        """Set a task's goal_id field."""
        task = await self.store.get_task(task_id)
        if task:
            task.goal_id = goal_id
            await self.store.save_task(task)
            logger.debug("Linked task {} to goal {}", task_id, goal_id)

    async def update_goal_status(self, goal_id: str, status: GoalStatus) -> None:
        goal = await self.store.get_goal(goal_id)
        if goal:
            goal.status = status
            await self.store.save_goal(goal)
            logger.info("Goal {} status -> {}", goal_id, status.value)

    async def list_root_goals(self, org_id: str) -> list[Goal]:
        return await self.store.list_goals(org_id, parent_id=None)

    async def list_children(self, goal_id: str) -> list[Goal]:
        goal = await self.store.get_goal(goal_id)
        if not goal:
            return []
        return await self.store.list_goals(goal.org_id, parent_id=goal_id)
