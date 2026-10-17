"""State models used by the AutoSwarm CLI board."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

PaneFocus = Literal["session-rail", "main", "context"]
ViewMode = Literal["kanban", "list", "focus", "pipeline", "org"]
ContextTab = Literal["detail", "session", "activity"]
DensityMode = Literal["compact", "comfortable"]


@dataclass
class PendingCheckpointView:
    checkpoint_id: str
    checkpoint_type: str
    status: str
    session_id: str | None
    task_id: str | None
    summary: str
    prompt: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def short_label(self) -> str:
        return f"{self.checkpoint_type} ({self.status})"


@dataclass
class LinkedExecutionView:
    task_id: str
    title: str
    status: str
    assigned_to: str
    session_id: str | None
    created_at: float
    updated_at: float
    runtime_task_id: str | None = None
    execution_turn_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionMessageView:
    message_id: str
    role: str
    sender_name: str
    content: str
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeTaskState:
    status: str = "idle"
    current_tool: str | None = None
    iteration: int = 0
    tool_elapsed_ms: int = 0
    last_tool_summary: str = ""
    context_tokens: int = 0
    context_window: int = 0
    context_remaining_pct: int = 0
    turn_cost_usd: float = 0.0
    session_cost_usd: float = 0.0
    pending_permission_count: int = 0
    drain_mode: str = "idle"
    progress_entries: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def push_progress(self, text: str, *, max_entries: int = 50) -> None:
        entry = str(text or "").strip()
        if not entry:
            return
        self.progress_entries.append(entry)
        if len(self.progress_entries) > max_entries:
            self.progress_entries = self.progress_entries[-max_entries:]
        self.updated_at = time.time()


@dataclass
class BoardAlert:
    alert_id: str
    level: str
    title: str
    message: str
    task_id: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class SessionSummaryView:
    task_id: str
    title: str
    status: str
    column_id: str
    session_id: str | None
    updated_at: float
    created_at: float
    assigned_to: str = ""
    priority: str | None = None
    pending_checkpoint: bool = False
    linked_task_count: int = 0
    tags: list[str] = field(default_factory=list)
    runtime_task_id: str | None = None
    execution_turn_id: str | None = None


@dataclass
class BoardMetrics:
    total_tasks: int = 0
    visible_tasks: int = 0
    filtered_tasks: int = 0
    hidden_task_count: int = 0
    todo_count: int = 0
    in_progress_count: int = 0
    in_review_count: int = 0
    done_count: int = 0
    running_count: int = 0
    blocked_count: int = 0
    failed_count: int = 0
    pending_checkpoint_count: int = 0
    active_session_count: int = 0
    stale_task_count: int = 0
    alert_count: int = 0
    last_refreshed_at: float = field(default_factory=time.time)
    last_runtime_update: float | None = None


@dataclass
class BoardTaskView:
    task_id: str
    title: str
    description: str
    status: str
    column_id: str
    priority: str | None
    assignee_ids: list[str] = field(default_factory=list)
    assigned_to: str = ""
    tags: list[str] = field(default_factory=list)
    session_id: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    pending_checkpoint: PendingCheckpointView | None = None
    linked_task_count: int = 0
    result_content: str | None = None
    artifacts: list[Any] = field(default_factory=list)
    origin_task_id: str | None = None
    display_id: str = ""
    dependencies: list[str] = field(default_factory=list)
    # Company-mode fields: card identity is the DelegationWorkItem.
    # `task_id` carries `work_item_id` in company mode; the runtime Task and its
    # session are referenced via runtime_task_id / execution_turn_id aliases.
    work_item_id: str | None = None
    runtime_task_id: str | None = None
    execution_turn_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {"done", "failed", "cancelled"}


@dataclass
class TaskDetailView:
    task: BoardTaskView
    transcript: list[SessionMessageView] = field(default_factory=list)
    linked_executions: list[LinkedExecutionView] = field(default_factory=list)
    progress_entries: list[str] = field(default_factory=list)
    pending_checkpoint: PendingCheckpointView | None = None
    result_content: str | None = None
    artifacts: list[Any] = field(default_factory=list)
    context_preview: str | None = None


@dataclass
class OrgRoleView:
    """One role in the org tree."""
    role_id: str
    name: str
    responsibility: str
    reports_to: str = "owner"
    employee_count: int = 0
    children: list[OrgRoleView] = field(default_factory=list)


@dataclass
class OrgEmployeeView:
    """One employee summary."""
    employee_id: str
    name: str
    role_id: str
    category: str = ""
    domains: list[str] = field(default_factory=list)
    seniority: str = "junior"


@dataclass
class OrgSnapshotView:
    """Read-only snapshot of org structure."""
    role_tree: list[OrgRoleView] = field(default_factory=list)
    employees: list[OrgEmployeeView] = field(default_factory=list)
    company_profile: str = ""
    role_count: int = 0
    employee_count: int = 0


@dataclass
class PipelineWorkItemView:
    """One work-item projection in a company-mode runtime pipeline."""
    projection_id: str
    title: str
    role_id: str
    status: str = "pending"          # pending | running | done | failed | cancelled | blocked
    assigned_to: str = ""
    task_id: str | None = None
    runtime_task_id: str | None = None
    execution_turn_id: str | None = None
    session_id: str | None = None
    elapsed_sec: float = 0.0
    current_tool: str | None = None
    tool_elapsed_ms: int = 0
    last_tool_summary: str = ""
    context_remaining_pct: int = 0
    turn_cost_usd: float = 0.0
    has_gate: bool = False
    gate_type: str | None = None     # review | approval | human_confirmation
    dependencies: list[str] = field(default_factory=list)
    parallel_group: str | None = None


@dataclass
class PipelineSnapshot:
    """Full pipeline state for the selected company-mode runtime."""
    parent_task_id: str = ""
    parent_title: str = ""
    profile: str = ""
    work_items: list[PipelineWorkItemView] = field(default_factory=list)
    done_count: int = 0
    total_count: int = 0
    elapsed_sec: float = 0.0


@dataclass
class BoardSnapshot:
    project_id: str
    tasks: list[BoardTaskView] = field(default_factory=list)
    hidden_task_count: int = 0
    pending_checkpoint_count: int = 0
    last_refreshed_at: float = field(default_factory=time.time)
    session_summaries: list[SessionSummaryView] = field(default_factory=list)
    alerts: list[BoardAlert] = field(default_factory=list)
    metrics: BoardMetrics = field(default_factory=BoardMetrics)
    # "standard" → cards are runtime Tasks; "company" → cards are DelegationWorkItems.
    mode: str = "standard"
    # When set, BoardStateStore adopts this column order (lets company mode
    # surface the in-review column without hardcoding it client-side).
    column_order: list[str] | None = None
