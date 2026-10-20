"""Company work-item read service."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from opc.layer2_organization.phase import coerce_phase, kanban_column, should_hide_work_item_from_company_kanban
from opc.layer2_organization.work_item_links import task_by_linked_work_item_id

from .context import OfficeServiceContext
from .models import ServiceError, ServiceResult


class WorkItemService:
    def __init__(self, context: OfficeServiceContext) -> None:
        self.context = context

    async def list(
        self,
        *,
        project_id: str,
        session_id: str | None = None,
        role_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        kanban_visible_only: bool = False,
    ) -> ServiceResult:
        session_id = str(session_id or "").strip()
        engine = await self.context.engine_for_project(project_id)
        store = getattr(engine, "store", None)
        if not self.context.store_is_ready(store):
            raise ServiceError("store_not_ready", "store_not_ready", {"project_id": project_id})
        runs = await self._runs(store, project_id, session_id=session_id or None)
        tasks = await store.get_tasks(project_id=project_id) if hasattr(store, "get_tasks") else []
        hydrate = getattr(store, "hydrate_task_work_item_links", None)
        if callable(hydrate):
            await hydrate(tasks)
        linked_tasks = task_by_linked_work_item_id(tasks)

        rows: list[dict[str, Any]] = []
        for run in runs:
            run_id = str(getattr(run, "run_id", "") or "")
            if not run_id:
                continue
            for item in await store.list_delegation_work_items(run_id):
                linked_task = linked_tasks.get(str(getattr(item, "work_item_id", "") or ""))
                if session_id and not self._matches_session_scope(session_id, run=run, item=item, linked_task=linked_task):
                    continue
                if kanban_visible_only and not self._is_company_kanban_visible(item):
                    continue
                payload = self._work_item_payload(item, run=run, linked_task=linked_task)
                if role_id and payload.get("role_id") != role_id:
                    continue
                if status and payload.get("phase") != status and payload.get("kanban_column") != status:
                    continue
                rows.append(payload)
        rows.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return ServiceResult({
            "project_id": project_id,
            "session_id": session_id,
            "work_items": rows[: max(1, int(limit or 100))],
        })

    async def show(self, *, project_id: str, work_item_id: str, limit: int = 100) -> ServiceResult:
        engine = await self.context.engine_for_project(project_id)
        store = getattr(engine, "store", None)
        if not self.context.store_is_ready(store):
            raise ServiceError("store_not_ready", "store_not_ready", {"project_id": project_id})
        runs = await self._runs(store, project_id)
        tasks = await store.get_tasks(project_id=project_id) if hasattr(store, "get_tasks") else []
        hydrate = getattr(store, "hydrate_task_work_item_links", None)
        if callable(hydrate):
            await hydrate(tasks)
        linked_tasks = task_by_linked_work_item_id(tasks)
        target = None
        target_run = None
        for run in runs:
            run_id = str(getattr(run, "run_id", "") or "")
            for item in await store.list_delegation_work_items(run_id):
                if str(getattr(item, "work_item_id", "") or "") == work_item_id:
                    target = item
                    target_run = run
                    break
            if target is not None:
                break
        if target is None:
            raise ServiceError("work_item_not_found", "Work item not found", {"work_item_id": work_item_id})
        linked_task = linked_tasks.get(work_item_id)
        logs = await self.logs(project_id=project_id, work_item_id=work_item_id, limit=limit)
        return ServiceResult({
            "project_id": project_id,
            "work_item": self._work_item_payload(target, run=target_run, linked_task=linked_task),
            "logs": logs.payload,
        })

    async def logs(
        self,
        *,
        project_id: str,
        session_id: str | None = None,
        work_item_id: str = "",
        role_id: str = "",
        limit: int = 100,
    ) -> ServiceResult:
        session_id = str(session_id or "").strip()
        work_item_id = str(work_item_id or "").strip()
        role_id = str(role_id or "").strip()
        engine = await self.context.engine_for_project(project_id)
        store = getattr(engine, "store", None)
        if not self.context.store_is_ready(store):
            raise ServiceError("store_not_ready", "store_not_ready", {"project_id": project_id})
        runs = await self._runs(store, project_id, session_id=session_id or None)
        tasks = await store.get_tasks(project_id=project_id) if hasattr(store, "get_tasks") else []
        hydrate = getattr(store, "hydrate_task_work_item_links", None)
        if callable(hydrate):
            await hydrate(tasks)
        linked_tasks = task_by_linked_work_item_id(tasks)

        target_items: list[tuple[Any, Any, Any | None]] = []
        target_work_item_ids: set[str] = set()
        for run in runs:
            run_id = str(getattr(run, "run_id", "") or "")
            if not run_id:
                continue
            for item in await store.list_delegation_work_items(run_id):
                item_id = str(getattr(item, "work_item_id", "") or "")
                if work_item_id and item_id != work_item_id:
                    continue
                if role_id and str(getattr(item, "role_id", "") or "") != role_id:
                    continue
                linked_task = linked_tasks.get(item_id)
                target_items.append((item, run, linked_task))
                if item_id:
                    target_work_item_ids.add(item_id)

        events: list[dict[str, Any]] = []
        target_run_ids = {str(getattr(run, "run_id", "") or "") for run in runs}
        for run_id in target_run_ids:
            if hasattr(store, "list_delegation_events"):
                for event in await store.list_delegation_events(run_id):
                    event_work_item_id = str(getattr(event, "work_item_id", "") or "")
                    event_role_id = str(getattr(event, "role_id", "") or "")
                    if work_item_id and event_work_item_id != work_item_id:
                        continue
                    if role_id and event_role_id != role_id and event_work_item_id not in target_work_item_ids:
                        continue
                    events.append(self._event_payload(event))

        runtime_sessions: list[dict[str, Any]] = []
        external_sessions: list[dict[str, Any]] = []
        runtime_events: list[dict[str, Any]] = []
        runtime_transcript_entries: list[dict[str, Any]] = []
        runtime_tool_calls: list[dict[str, Any]] = []
        runtime_tool_results: list[dict[str, Any]] = []
        runtime_permission_grants: list[dict[str, Any]] = []
        transcript: list[Any] = []
        handoffs: list[Any] = []

        runtime_rows_by_id: dict[str, dict[str, Any]] = {}
        runtime_ids: set[str] = set()
        external_keys: set[tuple[str, str, str]] = set()

        def add_runtime_row(row: Any) -> None:
            payload = self._model_payload(row)
            runtime_id = str(payload.get("runtime_session_id", "") or "").strip()
            if not runtime_id:
                return
            runtime_rows_by_id[runtime_id] = payload
            runtime_ids.add(runtime_id)

        def add_runtime_id(runtime_id: Any, *, task: Any = None, role_session: Any = None, source: str = "") -> None:
            runtime_id = str(runtime_id or "").strip()
            if not runtime_id:
                return
            runtime_ids.add(runtime_id)
            if runtime_id in runtime_rows_by_id:
                return
            metadata = {"source": source} if source else {}
            if role_session is not None:
                metadata.update({"source": source or "role_runtime_session", "role_id": str(getattr(role_session, "role_id", "") or "")})
            runtime_rows_by_id[runtime_id] = {
                "runtime_session_id": runtime_id,
                "project_id": project_id,
                "session_id": str(getattr(task, "session_id", "") or session_id or ""),
                "task_id": str(getattr(task, "id", "") or ""),
                "status": str(getattr(role_session, "status", "") or ""),
                "metadata": metadata,
                "created_at": self._date_value(getattr(role_session, "created_at", None)),
                "updated_at": self._date_value(getattr(role_session, "updated_at", None)),
            }

        def add_external_session(row: Any) -> None:
            payload = self._model_payload(row)
            key = (
                str(payload.get("agent_type", "") or ""),
                str(payload.get("session_id", "") or ""),
                str(payload.get("task_id", "") or ""),
            )
            if key in external_keys:
                return
            external_keys.add(key)
            external_sessions.append(payload)
            metadata = dict(payload.get("metadata", {}) or {})
            add_runtime_id(metadata.get("runtime_session_id"), source="external_session")
            add_runtime_id(metadata.get("delegation_role_session_id"), source="external_role_session")
            add_runtime_id(payload.get("opc_session_id"), source="external_opc_session")

        for item, run, linked_task in target_items:
            item_id = str(getattr(item, "work_item_id", "") or "")
            if linked_task is not None:
                linked_session_id = str(getattr(linked_task, "session_id", "") or "")
                if linked_session_id and hasattr(store, "get_session_transcript"):
                    transcript.extend((await store.get_session_transcript(linked_session_id))[-limit:])
                if hasattr(store, "list_runtime_sessions"):
                    for row in await store.list_runtime_sessions(project_id=project_id, task_id=getattr(linked_task, "id", ""), limit=limit):
                        add_runtime_row(row)
                    if linked_session_id:
                        for row in await store.list_runtime_sessions(project_id=project_id, session_id=linked_session_id, limit=limit):
                            add_runtime_row(row)
                if hasattr(store, "list_external_sessions"):
                    for row in await store.list_external_sessions(project_id=project_id, task_id=getattr(linked_task, "id", ""), limit=limit):
                        add_external_session(row)
                for runtime_id in self._task_runtime_session_ids(linked_task):
                    add_runtime_id(runtime_id, task=linked_task, source="task_metadata")
            for runtime_id in self._work_item_runtime_session_ids(item):
                add_runtime_id(runtime_id, task=linked_task, source="work_item")
            if hasattr(store, "get_handoff_records") and item_id:
                handoffs.extend(await store.get_handoff_records(project_id=project_id, target_work_item_id=item_id, limit=limit))

        if hasattr(store, "list_role_runtime_sessions"):
            for run in runs:
                run_id = str(getattr(run, "run_id", "") or "")
                if not run_id:
                    continue
                try:
                    role_sessions = await store.list_role_runtime_sessions(run_id, role_id=role_id or None)
                except TypeError:
                    role_sessions = await store.list_role_runtime_sessions(run_id)
                    if role_id:
                        role_sessions = [item for item in role_sessions if str(getattr(item, "role_id", "") or "") == role_id]
                for role_session in role_sessions:
                    role_session_id = str(getattr(role_session, "role_session_id", "") or "")
                    focused = str(getattr(role_session, "focused_work_item_id", "") or "")
                    related = {focused}
                    related.update(str(item or "").strip() for item in list(getattr(role_session, "background_work_item_ids", []) or []))
                    related.update(str(item or "").strip() for item in list(getattr(role_session, "pending_work_item_ids", []) or []))
                    if work_item_id and work_item_id not in related:
                        continue
                    add_runtime_id(role_session_id, role_session=role_session, source="role_runtime_session")
                    if hasattr(store, "list_external_sessions") and role_session_id:
                        for row in await store.list_external_sessions(project_id=project_id, opc_session_id=role_session_id, limit=limit):
                            add_external_session(row)

        for runtime_id in sorted(runtime_ids):
            if hasattr(store, "list_runtime_events"):
                runtime_events.extend(await store.list_runtime_events(runtime_id, limit=limit))
            if hasattr(store, "list_runtime_transcript_entries"):
                runtime_transcript_entries.extend((await store.list_runtime_transcript_entries(runtime_id))[-limit:])
            if hasattr(store, "list_runtime_tool_calls"):
                runtime_tool_calls.extend((await store.list_runtime_tool_calls(runtime_id))[-limit:])
            if hasattr(store, "list_runtime_tool_results"):
                runtime_tool_results.extend((await store.list_runtime_tool_results(runtime_id))[-limit:])
            if hasattr(store, "list_runtime_permission_grants"):
                runtime_permission_grants.extend((await store.list_runtime_permission_grants(runtime_session_id=runtime_id))[-limit:])

        events.sort(key=lambda item: str(item.get("created_at", "")))
        runtime_sessions = list(runtime_rows_by_id.values())
        runtime_sessions.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return ServiceResult({
            "project_id": project_id,
            "session_id": session_id,
            "work_item_id": work_item_id,
            "role_id": role_id,
            "work_items": [
                self._work_item_payload(item, run=run, linked_task=linked_task)
                for item, run, linked_task in target_items
            ][:limit],
            "events": events[-limit:],
            "runtime_sessions": runtime_sessions[:limit],
            "external_sessions": external_sessions[-limit:],
            "runtime_events": runtime_events[-limit:],
            "runtime_transcript_entries": runtime_transcript_entries[-limit:],
            "runtime_tool_calls": runtime_tool_calls[-limit:],
            "runtime_tool_results": runtime_tool_results[-limit:],
            "runtime_permission_grants": runtime_permission_grants[-limit:],
            "transcript": transcript,
            "handoffs": [self._model_payload(item) for item in handoffs],
        })

    async def status_by_role(self, *, project_id: str, session_id: str | None = None) -> ServiceResult:
        listing = await self.list(project_id=project_id, session_id=session_id, limit=10000)
        by_role: dict[str, Counter[str]] = defaultdict(Counter)
        for item in listing.payload.get("work_items", []):
            role_id = str(item.get("role_id") or "unassigned")
            by_role[role_id][str(item.get("kanban_column") or item.get("phase") or "unknown")] += 1
        return ServiceResult({
            "project_id": project_id,
            "session_id": str(session_id or "").strip(),
            "roles": [
                {"role_id": role_id, "counts": dict(counts), "total": sum(counts.values())}
                for role_id, counts in sorted(by_role.items())
            ],
        })

    async def role_detail(self, *, project_id: str, role_id: str, limit: int = 100) -> ServiceResult:
        role_id = str(role_id or "").strip()
        if not role_id:
            raise ServiceError("role_id_required", "role_id required")
        listing = await self.list(project_id=project_id, role_id=role_id, limit=limit)
        logs = await self.logs(project_id=project_id, role_id=role_id, limit=limit)
        counts: Counter[str] = Counter()
        for item in listing.payload.get("work_items", []):
            counts[str(item.get("kanban_column") or item.get("phase") or "unknown")] += 1
        return ServiceResult({
            "project_id": project_id,
            "role_id": role_id,
            "counts": dict(counts),
            "work_items": listing.payload.get("work_items", []),
            "logs": logs.payload,
        })

    async def _runs(self, store: Any, project_id: str, *, session_id: str | None = None) -> list[Any]:
        session_id = str(session_id or "").strip()
        if hasattr(store, "list_open_delegation_runs"):
            runs = await store.list_open_delegation_runs(project_id=project_id)
            if session_id:
                matched = [run for run in runs if self._session_id_matches(session_id, getattr(run, "session_id", ""))]
                if matched:
                    return list(matched)
            elif runs:
                return list(runs)
        if hasattr(store, "list_delegation_runs"):
            if session_id:
                for candidate_session_id in self._session_scope_candidates(session_id):
                    try:
                        runs = await store.list_delegation_runs(project_id=project_id, session_id=candidate_session_id)
                    except TypeError:
                        break
                    if runs:
                        return list(runs)
            runs = list(await store.list_delegation_runs(project_id=project_id))
            if session_id:
                return [run for run in runs if self._session_id_matches(session_id, getattr(run, "session_id", ""))]
            return runs
        return []

    @staticmethod
    def _is_company_kanban_visible(item: Any) -> bool:
        metadata = dict(getattr(item, "metadata", {}) or {})
        return bool(
            str(getattr(item, "parent_work_item_id", "") or "").strip()
            and not bool(metadata.get("attention_work_item", False))
            and not should_hide_work_item_from_company_kanban(metadata)
        )

    @classmethod
    def _matches_session_scope(cls, session_id: str, *, run: Any = None, item: Any = None, linked_task: Any = None) -> bool:
        session_id = str(session_id or "").strip()
        if not session_id:
            return True
        for candidate in cls._session_candidates(run=run, item=item, linked_task=linked_task):
            if cls._session_id_matches(session_id, candidate):
                return True
        return False

    @staticmethod
    def _session_scope_candidates(session_id: str) -> list[str]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return []
        candidates = [session_id]
        if ":" in session_id:
            candidates.append(session_id.split(":", 1)[0])
        return list(dict.fromkeys(candidates))

    @classmethod
    def _session_candidates(cls, *, run: Any = None, item: Any = None, linked_task: Any = None) -> list[str]:
        values: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text:
                values.append(text)

        def add_from_mapping(mapping: Any) -> None:
            if not isinstance(mapping, dict):
                return
            for key in (
                "session_id",
                "parent_session_id",
                "root_session_id",
                "origin_session_id",
                "opc_session_id",
                "company_runtime_root_session_id",
            ):
                add(mapping.get(key))

        if run is not None:
            add(getattr(run, "session_id", ""))
            add_from_mapping(getattr(run, "metadata", {}) or {})
            add_from_mapping(getattr(run, "recovery_pointer", {}) or {})
        if item is not None:
            add_from_mapping(getattr(item, "metadata", {}) or {})
        if linked_task is not None:
            add(getattr(linked_task, "session_id", ""))
            add(getattr(linked_task, "parent_session_id", ""))
            add_from_mapping(getattr(linked_task, "metadata", {}) or {})
        return list(dict.fromkeys(values))

    @staticmethod
    def _session_id_matches(scope_session_id: str, candidate_session_id: Any) -> bool:
        scope = str(scope_session_id or "").strip()
        candidate = str(candidate_session_id or "").strip()
        if not scope or not candidate:
            return False
        return (
            scope == candidate
            or candidate.startswith(f"{scope}:")
            or scope.startswith(f"{candidate}:")
        )

    @classmethod
    def _task_runtime_session_ids(cls, task: Any) -> list[str]:
        metadata = dict(getattr(task, "metadata", {}) or {})
        context_snapshot = dict(getattr(task, "context_snapshot", {}) or {})
        values = [
            (metadata.get("runtime_v2", {}) or {}).get("runtime_session_id") if isinstance(metadata.get("runtime_v2"), dict) else "",
            (context_snapshot.get("runtime_resume", {}) or {}).get("runtime_session_id") if isinstance(context_snapshot.get("runtime_resume"), dict) else "",
            metadata.get("_permission_bridge_runtime_session_id"),
            metadata.get("delegation_role_session_id"),
            metadata.get("assigned_role_runtime_id"),
            metadata.get("role_runtime_session_id"),
            metadata.get("runtime_session_id"),
        ]
        return cls._dedupe_text(values)

    @classmethod
    def _work_item_runtime_session_ids(cls, item: Any) -> list[str]:
        metadata = dict(getattr(item, "metadata", {}) or {})
        values = [
            getattr(item, "role_runtime_session_id", ""),
            getattr(item, "claimed_by_role_runtime_session_id", ""),
            metadata.get("assigned_role_runtime_id"),
            metadata.get("role_runtime_session_id"),
            metadata.get("claimed_by_role_runtime_session_id"),
            metadata.get("delegation_role_session_id"),
            metadata.get("runtime_session_id"),
        ]
        return cls._dedupe_text(values)

    @staticmethod
    def _dedupe_text(values: list[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _work_item_payload(self, item: Any, *, run: Any = None, linked_task: Any = None) -> dict[str, Any]:
        phase = coerce_phase(getattr(item, "phase", ""))
        phase_value = phase.value if hasattr(phase, "value") else str(phase or "")
        return {
            "work_item_id": str(getattr(item, "work_item_id", "") or ""),
            "run_id": str(getattr(item, "run_id", "") or ""),
            "project_id": str(getattr(run, "project_id", "") or ""),
            "title": str(getattr(item, "title", "") or ""),
            "summary": str(getattr(item, "summary", "") or ""),
            "role_id": str(getattr(item, "role_id", "") or ""),
            "seat_id": str(getattr(item, "seat_id", "") or ""),
            "manager_role_id": str(getattr(item, "manager_role_id", "") or ""),
            "parent_work_item_id": str(getattr(item, "parent_work_item_id", "") or ""),
            "role_runtime_session_id": str(getattr(item, "role_runtime_session_id", "") or ""),
            "claimed_by_role_runtime_session_id": str(getattr(item, "claimed_by_role_runtime_session_id", "") or ""),
            "phase": phase_value,
            "kanban_column": kanban_column(phase),
            "deliverable_summary": str(getattr(item, "deliverable_summary", "") or ""),
            "blocked_reason": str(getattr(item, "blocked_reason", "") or ""),
            "handoff_status": str(getattr(item, "handoff_status", "") or ""),
            "metadata": dict(getattr(item, "metadata", {}) or {}),
            "runtime_task_id": str(getattr(linked_task, "id", "") or "") if linked_task is not None else "",
            "session_id": str(getattr(linked_task, "session_id", "") or "") if linked_task is not None else "",
            "runtime_status": (
                getattr(getattr(linked_task, "status", None), "value", getattr(linked_task, "status", ""))
                if linked_task is not None else ""
            ),
            "created_at": self._date_value(getattr(item, "created_at", None)),
            "updated_at": self._date_value(getattr(item, "updated_at", None)),
        }

    @staticmethod
    def _event_payload(event: Any) -> dict[str, Any]:
        return {
            "event_id": str(getattr(event, "event_id", "") or ""),
            "run_id": str(getattr(event, "run_id", "") or ""),
            "work_item_id": str(getattr(event, "work_item_id", "") or ""),
            "cell_id": str(getattr(event, "cell_id", "") or ""),
            "role_id": str(getattr(event, "role_id", "") or ""),
            "event_type": str(getattr(event, "event_type", "") or ""),
            "payload": dict(getattr(event, "payload", {}) or {}),
            "created_at": WorkItemService._date_value(getattr(event, "created_at", None)),
        }

    @staticmethod
    def _model_payload(value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        if isinstance(value, dict):
            return dict(value)
        return {"value": value}

    @staticmethod
    def _date_value(value: Any) -> Any:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value
