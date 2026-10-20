"""Task graph scheduler — DAG-based task dependency management and parallel execution."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from loguru import logger

from opc.core.models import Task, TaskStatus, OPCEvent
from opc.core.events import EventBus
from opc.database.store import OPCStore
from opc.layer2_organization.work_item_identity import work_item_projection_id_from_metadata


class TaskGraphScheduler:
    """Manages task dependencies as a DAG and schedules execution.

    Tasks with no unmet dependencies are marked RUNNABLE.
    Independent tasks can run in parallel; dependent tasks wait.
    """

    def __init__(self, store: OPCStore, event_bus: EventBus) -> None:
        self.store = store
        self.event_bus = event_bus

    async def create_tasks(self, task_dicts: list[dict[str, Any]], parent_id: str | None = None) -> list[Task]:
        """Create tasks from dispatch plan and save to store."""
        tasks: list[Task] = []
        id_map: dict[int, str] = {}
        logical_id_map: dict[str, str] = {}

        for i, td in enumerate(task_dicts):
            metadata = td.get("metadata", {})
            task = Task(
                session_id=td.get("session_id"),
                parent_session_id=td.get("parent_session_id"),
                title=td.get("title", ""),
                description=td.get("description", ""),
                assigned_to=td.get("assigned_to", ""),
                tags=td.get("tags", []),
                priority=td.get("priority", 5),
                project_id=td.get("project_id", "default"),
                parent_id=parent_id,
                assigned_external_agent=td.get("assigned_external_agent"),
                metadata=metadata,
            )
            id_map[i] = task.id
            logical_key = td.get("task_key") or work_item_projection_id_from_metadata(metadata) or metadata.get("task_key")
            if logical_key:
                logical_id_map[str(logical_key)] = task.id
            tasks.append(task)

        for i, td in enumerate(task_dicts):
            dep_indices = td.get("dependencies", [])
            deps: list[str] = []
            for dep in dep_indices:
                if isinstance(dep, int) and dep in id_map:
                    deps.append(id_map[dep])
                elif isinstance(dep, str) and dep in logical_id_map:
                    deps.append(logical_id_map[dep])
                elif isinstance(dep, str):
                    deps.append(dep)
            tasks[i].dependencies = deps

        for task in tasks:
            await self.store.save_task(task)
            await self.event_bus.publish(OPCEvent(
                event_type="task_created",
                payload={"task_id": task.id, "title": task.title},
            ))

        return tasks

    def get_runnable(self, tasks: list[Task]) -> list[Task]:
        """Return tasks whose dependencies are all DONE."""
        done_ids = {t.id for t in tasks if t.status == TaskStatus.DONE}
        runnable: list[Task] = []
        for task in tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if all(dep in done_ids for dep in task.dependencies):
                runnable.append(task)
        return runnable

    async def execute_graph(
        self,
        tasks: list[Task],
        executor: Callable[[Task], Coroutine[Any, Any, Any]],
    ) -> list[Task]:
        """Execute a task graph, respecting dependencies.

        Runs independent tasks in parallel, waits for dependent tasks.
        """
        remaining = set(t.id for t in tasks if t.status == TaskStatus.PENDING)
        task_map = {t.id: t for t in tasks}

        while remaining:
            current_tasks = [task_map[tid] for tid in remaining]
            runnable = self.get_runnable(current_tasks + [t for t in tasks if t.status == TaskStatus.DONE])

            if not runnable:
                failed = [task_map[tid] for tid in remaining]
                blocked_ids = [t.id for t in failed]
                logger.warning(f"No runnable tasks found. Blocked: {blocked_ids}")
                for t in failed:
                    t.status = TaskStatus.BLOCKED
                    await self.store.save_task(t)
                break

            logger.info(f"Running {len(runnable)} tasks in parallel")

            async def _run_one(task: Task) -> None:
                try:
                    task.status = TaskStatus.RUNNING
                    await self.store.save_task(task)
                    await self.event_bus.publish(OPCEvent(
                        event_type="task_status_changed",
                        payload={"task_id": task.id, "status": "running"},
                    ))

                    await executor(task)

                except Exception as e:
                    logger.error(f"Task {task.id} failed: {e}")
                    task.status = TaskStatus.FAILED
                    await self.store.save_task(task)
                finally:
                    # Notify frontend of final task status (DONE/FAILED/etc.)
                    await self.event_bus.publish(OPCEvent(
                        event_type="task_status_changed",
                        payload={"task_id": task.id, "status": task.status.value},
                    ))

            await asyncio.gather(*[_run_one(t) for t in runnable])

            for t in runnable:
                remaining.discard(t.id)

        return tasks

    async def get_all_project_tasks(self, project_id: str) -> list[Task]:
        return await self.store.get_tasks(project_id=project_id)
