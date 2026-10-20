from __future__ import annotations

import re
from pathlib import Path

from opc.plugins.office_ui.snapshot_builder import _work_item_entry_from_progress
from opc.plugins.office_ui.ws_handler import WSHandler


REPO_ROOT = Path(__file__).resolve().parents[1]


def _production_python_files() -> list[Path]:
    files: list[Path] = []
    for path in (REPO_ROOT / "opc").rglob("*.py"):
        parts = set(path.parts)
        if "frontend_dist" in parts or "node_modules" in parts or "tests" in parts:
            continue
        files.append(path)
    return files


def _without_allowed_non_company_terms(files: list[Path]) -> list[Path]:
    allowed = {
        "opc/layer4_tools/git_ops.py",
    }
    return [
        path for path in files
        if path.relative_to(REPO_ROOT).as_posix() not in allowed
    ]


def _ordinary_test_files() -> list[Path]:
    files: list[Path] = []
    for path in (REPO_ROOT / "tests").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.endswith("_guard.py"):
            continue
        files.append(path)
    return files


def _format_matches(matches: list[tuple[Path, int, str]]) -> str:
    return "\n".join(
        f"{path.relative_to(REPO_ROOT)}:{line_no}: {line.strip()}"
        for path, line_no, line in matches
    )


def _scan(pattern: str, files: list[Path]) -> list[tuple[Path, int, str]]:
    regex = re.compile(pattern)
    matches: list[tuple[Path, int, str]] = []
    for path in files:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if regex.search(line):
                matches.append((path, line_no, line))
    return matches


def test_production_python_has_no_legacy_projection_identity_keys() -> None:
    # Mirrors:
    # rg 'workflow_stage_id|company_stage_type' opc --glob '!**/frontend_dist/**'
    matches = _scan(
        r"workflow_stage_id|company_stage_type",
        _production_python_files(),
    )
    assert not matches, _format_matches(matches)


def test_production_python_has_no_runtime_graph_marker_fallback() -> None:
    # Mirrors:
    # rg 'runtime_graph|delegation_runtime_graph' opc --glob '!**/frontend_dist/**'
    matches = _scan(
        r"runtime_graph|delegation_runtime_graph",
        _production_python_files(),
    )
    assert not matches, _format_matches(matches)


def test_production_python_has_no_obsolete_db_compat_names() -> None:
    matches = _scan(
        r"\bstage_id\b|stage_task_id|source_stage_id|target_stage_id|"
        r"workflow_profile|workflow_decisions",
        _production_python_files(),
    )
    assert not matches, _format_matches(matches)


def test_company_work_item_executor_has_no_legacy_workflow_alias() -> None:
    legacy_name = "Company" + "WorkflowExecutor"
    matches = _scan(rf"\b{legacy_name}\b", _production_python_files())
    assert not matches, _format_matches(matches)


def test_company_mode_work_item_runtime_has_projection_naming_guards() -> None:
    files = [
        REPO_ROOT / "opc/layer2_organization/company_mode.py",
        REPO_ROOT / "opc/engine.py",
        REPO_ROOT / "opc/layer1_perception/context_assembler.py",
    ]
    matches = _scan(
        r"claimed stage|Company stage|_collect_downstream_stage_ids|dependency_stage_ids|fallback_stage_ids|"
        r"seen_stage_ids|company_stage_type|stage_assignment|stage_runtime_plan|stage_summary|"
        r"stage_artifact_index|stage_orchestration_profile|stage_verification_required|"
        r"AdaptiveStageProfile|stage_profile|stage_type",
        files,
    )
    assert not matches, _format_matches(matches)


def test_gate_rework_runtime_paths_are_projection_only() -> None:
    files = [
        REPO_ROOT / "opc/layer2_organization/gate_harness.py",
        REPO_ROOT / "opc/layer2_organization/work_item_identity.py",
        REPO_ROOT / "opc/layer2_organization/company_mode.py",
        REPO_ROOT / "opc/engine.py",
    ]
    matches = _scan(
        r"target_stage_id|target_stage_ids|preserve_stage_ids|review_stage_id|"
        r"rework_stage_id|rework_same_stage|upstream_.*source_stage_id",
        files,
    )
    assert not matches, _format_matches(matches)


def test_company_runtime_protocol_uses_work_item_event_names() -> None:
    matches = _scan(
        r"member_claimed_stage|stage_started|stage_failed|STAGE_RESULT|stage_result|rerun_stage",
        _production_python_files(),
    )
    assert not matches, _format_matches(matches)


def test_company_runtime_has_no_old_config_or_policy_names() -> None:
    production_matches = _scan(
        r"workflow_profile|workflow_policies|workflow_presets|"
        r"WorkflowExecutionStrategy|enable_stage_gates|workflow_parallel_group|"
        r"_workflow_plan_stages|workflow_release_decisions|workflow_selection|stage_feedback",
        _production_python_files(),
    )
    assert not production_matches, _format_matches(production_matches)

    test_matches = _scan(
        r"workflow_profile|workflow_policies|workflow_presets|"
        r"WorkflowExecutionStrategy|enable_stage_gates|workflow_parallel_group|"
        r"_workflow_plan_stages|workflow_release_decisions|workflow_selection|stage_feedback",
        _ordinary_test_files(),
    )
    assert not test_matches, _format_matches(test_matches)


def test_company_runtime_uses_work_item_org_engine_api_names() -> None:
    files = [
        REPO_ROOT / "opc/engine.py",
        REPO_ROOT / "opc/layer2_organization/company_mode.py",
        REPO_ROOT / "opc/layer2_organization/org_engine.py",
        REPO_ROOT / "opc/layer3_agent/company_runtime_contract.py",
        REPO_ROOT / "opc/layer3_agent/native_agent.py",
    ]
    matches = _scan(
        r"get_role_for_stage|resolve_employee_for_stage|stage_metadata|stage_tokens|"
        r"build_company_stage_contract|workflow_tasks|company_workflow_gate",
        files,
    )
    assert not matches, _format_matches(matches)


def test_company_runtime_uses_work_item_progress_and_gate_names() -> None:
    files = [
        REPO_ROOT / "opc/plugins/office_ui/ws_handler.py",
        REPO_ROOT / "opc/plugins/office_ui/snapshot_builder.py",
        REPO_ROOT / "opc/layer2_organization/company_mode.py",
        REPO_ROOT / "opc/layer2_organization/communication.py",
        REPO_ROOT / "opc/layer2_organization/collaboration_policy.py",
        REPO_ROOT / "opc/engine.py",
    ]
    matches = _scan(
        r"workflow_progress|WorkflowProgressEntry|WorkflowProgressPayload|"
        r"workflowLog|workflow_log|onWorkflowProgress|"
        r"workflow_gate|workflowGate|WorkflowGate|CompanyWorkflowGateMeta",
        files,
    )
    assert not matches, _format_matches(matches)


def test_company_mode_projection_spec_variables_are_projection_named() -> None:
    company_mode = REPO_ROOT / "opc/layer2_organization/company_mode.py"
    matches = _scan(
        r"stage: WorkItemProjectionSpec|for stage in plan\.projections|"
        r"stage = self\._projection_spec|stage = self\._task_effective_projection_spec|"
        r"stage=stage|stage\.projection_id|stage\.dependency_projection_ids|"
        r"stage\.metadata|stage\.summary|stage\.title",
        [company_mode],
    )
    assert not matches, _format_matches(matches)


def test_snapshot_work_item_progress_outputs_projection_fields_only() -> None:
    entry = _work_item_entry_from_progress(
        {
            "type": "work_item_started",
            "work_item_projection_id": "engineering_execution",
            "work_item_projection_title": "Engineering Execution",
            "detail": "projection input parses",
            "timestamp": 1.0,
        },
        runtime_task_id="task-1",
    )

    assert entry is not None
    assert entry["work_item_projection_id"] == "engineering_execution"
    assert entry["work_item_projection_title"] == "Engineering Execution"
    obsolete_entry_key = "work_item_" + "task_id"
    assert obsolete_entry_key not in entry
    assert entry["runtime_task_id"] == "task-1"
    assert entry["execution_turn_id"] == "task-1"
    assert "projection_id" not in entry
    assert "stage_title" not in entry
    assert "stage_task_id" not in entry


def test_runtime_work_item_progress_outputs_projection_fields_only() -> None:
    entry = WSHandler._runtime_event_to_progress_entry(
        {
            "type": "member_claimed_work_item",
            "work_item_projection_id": "engineering_execution",
            "work_item_projection_title": "Engineering Execution",
            "message_priority": "manager",
        }
    )

    assert entry is not None
    assert entry["work_item_projection_id"] == "engineering_execution"
    assert entry["work_item_projection_title"] == "Engineering Execution"
    assert "projection_id" not in entry
    assert "stage_title" not in entry


def test_frontend_work_item_progress_mapping_is_projection_only() -> None:
    collab_sync = (REPO_ROOT / "opc/plugins/office_ui/frontend_src/lib/collabSync.ts").read_text(
        encoding="utf-8"
    )
    app = (REPO_ROOT / "opc/plugins/office_ui/frontend_src/App.tsx").read_text(encoding="utf-8")
    kanban_types = (REPO_ROOT / "opc/plugins/office_ui/frontend_src/types/kanban.ts").read_text(
        encoding="utf-8"
    )

    assert "workItemProjectionTitle" in collab_sync
    obsolete_frontend_key = "workItem" + "TaskId"
    assert obsolete_frontend_key not in collab_sync
    forbidden = (
        "entry.projection_id",
        "entry.stageId",
        "entry.stage_title",
        "entry.stageTitle",
        "entry.stageTaskId",
        "stageId:",
        "stageTitle:",
        "stageTaskId:",
    )
    for token in forbidden:
        assert token not in collab_sync
        assert token not in app
    assert "stageId?:" not in kanban_types
    assert "stageTitle?:" not in kanban_types
    assert "stageTaskId?:" not in kanban_types


def test_company_ui_naming_aliases_are_present() -> None:
    frontend_root = REPO_ROOT / "opc/plugins/office_ui/frontend_src"
    kanban_types = (frontend_root / "types/kanban.ts").read_text(encoding="utf-8")
    collab_sync = (frontend_root / "lib/collabSync.ts").read_text(encoding="utf-8")
    runtime_ids = (frontend_root / "lib/workItemRuntimeIds.ts").read_text(encoding="utf-8")
    task_detail = (frontend_root / "workspace/TaskDetailView.tsx").read_text(encoding="utf-8")
    execution_panel = (frontend_root / "kanban/ExecutionPanel.tsx").read_text(encoding="utf-8")
    progress_card = (frontend_root / "chat/WorkItemProgressCard.tsx").read_text(encoding="utf-8")

    assert "export type WorkItemCard = KanbanTask" in kanban_types
    assert "export type RuntimeSession = Session" in kanban_types
    assert "export type ExecutionTurn = Session" in kanban_types
    for token in ("runtimeTaskId", "executionTurnId"):
        assert token in kanban_types
        assert token in collab_sync
    for helper in ("getWorkItemCardId", "getExecutionTurnId", "getLinkedRuntimeTaskId"):
        assert helper in runtime_ids
    assert "Open Runtime Session" in task_detail
    assert "Runtime Session Activity" in task_detail
    assert "Execution Turn" in execution_panel
    assert "Open Runtime Session" in progress_card


def test_office_ui_project_scope_is_explicit_not_defaulted() -> None:
    frontend_root = REPO_ROOT / "opc/plugins/office_ui/frontend_src"
    app = (frontend_root / "App.tsx").read_text(encoding="utf-8")
    ws_client = (frontend_root / "lib/wsClient.ts").read_text(encoding="utf-8")
    session_store = (frontend_root / "stores/SessionStore.ts").read_text(encoding="utf-8")
    ws_handler = (REPO_ROOT / "opc/plugins/office_ui/ws_handler.py").read_text(encoding="utf-8")

    assert "activeProjectIdRef.current = projectStore.activeProjectId" not in app
    assert "createSession(projectId: string" in ws_client
    assert "sessionSend(\n    projectId: string" in ws_client
    assert "commsState(projectId: string" in ws_client
    assert "project_id required for project-scoped request" in ws_handler
    assert "backendSessions.filter(session => session.projectId === nextProjectId)" in session_store


def test_company_ui_payload_paths_do_not_use_legacy_runtime_aliases() -> None:
    frontend_root = REPO_ROOT / "opc/plugins/office_ui/frontend_src"
    frontend_files = [
        path
        for path in frontend_root.rglob("*")
        if path.suffix in {".ts", ".tsx"} and "frontend_dist" not in path.parts
    ]
    backend_files = [
        REPO_ROOT / "opc/plugins/office_ui/ws_handler.py",
        REPO_ROOT / "opc/plugins/office_ui/snapshot_builder.py",
        REPO_ROOT / "opc/plugins/cli_board/services/board_repository.py",
        REPO_ROOT / "opc/plugins/cli_board/state/models.py",
    ]
    obsolete_alias_pattern = "|".join(
        (
            "workItem" + "TaskId",
            "work_item_" + "task_id",
            "linkedSession" + "TaskId",
            "linked_session_" + "task_id",
        )
    )
    matches = _scan(obsolete_alias_pattern, [*frontend_files, *backend_files])
    assert not matches, _format_matches(matches)


def test_legacy_work_item_runtime_link_fields_are_migration_only() -> None:
    legacy_link_key = "delegation_" + "work_item_id"
    legacy_column = "work_item_" + "task_id"
    legacy_wire_aliases = (
        "workItem" + "TaskId",
        "linkedSession" + "TaskId",
        "linked_session_" + "task_id",
    )
    allowed = {
        "opc/database/store.py",
        "tests/test_store_schema_migration.py",
    }
    files = [
        path
        for root in ("opc", "tests", "docs")
        for path in (REPO_ROOT / root).rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ts", ".tsx", ".md"}
        and "frontend_dist" not in path.parts
    ]
    pattern = "|".join((legacy_link_key, legacy_column, *legacy_wire_aliases))
    matches = [
        match
        for match in _scan(pattern, files)
        if match[0].relative_to(REPO_ROOT).as_posix() not in allowed
    ]
    assert not matches, _format_matches(matches)


def test_office_ui_work_item_paths_do_not_use_legacy_projection_identity_names() -> None:
    frontend_root = REPO_ROOT / "opc/plugins/office_ui/frontend_src"
    frontend_files = [
        path
        for path in frontend_root.rglob("*")
        if path.suffix in {".ts", ".tsx", ".css", ".html"}
        and "frontend_dist" not in path.parts
    ]
    frontend_matches = _scan(
        r"workflowStageId|companyStageType|stage-divider|wf-stage-|kanban-stage-badge|task-stage-pill|"
        r"WorkflowProgressCard|WorkflowRecoveryPanel|onStageClick|onStageOpenSession|"
        r"stageId:|stageTitle:|stageTaskId:|stageId\?:|stageTitle\?:|stageTaskId\?:|"
        r"wfr-stage|wfr-stages|stage_changes|replacement_projection_id|resumed_projection_ids|"
        r"member_claimed_stage|stage_started|stage_failed|workflowRoleId|workflowRoleName|"
        r"isWorkflow|setWorkflow|workflow-card|WorkflowIdentity|getWorkflowEmployeeLabel|"
        r"InterruptedWorkflow|ctx-child-stage|ckpt-stage-tag|IconStage|"
        r"workflow_replan|propose_workflow_replan",
        frontend_files,
    )
    assert not frontend_matches, _format_matches(frontend_matches)

    backend_matches = _scan(
        r"workflow_stage_id|company_stage_type|stage_changes|replacement_stage_id|resumed_stage_ids",
        [
            REPO_ROOT / "opc/plugins/office_ui/ws_handler.py",
            REPO_ROOT / "opc/plugins/office_ui/snapshot_builder.py",
        ],
    )
    assert not backend_matches, _format_matches(backend_matches)


def test_active_paths_have_no_legacy_workflow_stage_prompt_or_api_terms() -> None:
    """Keep active company runtime code, prompts, and UI copy projection-only.

    DB migration strings and genuine Git staging terminology are intentionally
    outside this guard.
    """
    production_matches = _scan(
        r"authorize_workflow_action|authorize_workflow_permission_decision|"
        r"\bstage_name\b|derived_stage_projection|_is_high_risk_stage|"
        r"InterruptedWorkflow|RecoverableStage|source_stage_title|review_stage_title|"
        r"workflow decision|workflow replan|SAME stage|this stage|"
        r"Stage Assignment|Stage Runtime|Stage Artifact|"
        r"stage_summaries_for_downstream|stage_completion|record_stage_completion|"
        r"WorkflowRecoveryManager|workflow executed|workflow resumed|workflow as accepted|"
        r"staged_assets|staged assets|stage files|stage_swarm|propose_workflow_replan",
        _without_allowed_non_company_terms(_production_python_files()),
    )
    assert not production_matches, _format_matches(production_matches)

    config_matches = _scan(
        r"stage_swarm|propose_workflow_replan|workflow_profile|workflow_policies|workflow_presets",
        [REPO_ROOT / "config/system_config.yaml"],
    )
    assert not config_matches, _format_matches(config_matches)

    frontend_root = REPO_ROOT / "opc/plugins/office_ui/frontend_src"
    frontend_files = [
        path
        for path in frontend_root.rglob("*")
        if path.suffix in {".ts", ".tsx", ".css", ".html"}
        and "frontend_dist" not in path.parts
    ]
    frontend_matches = _scan(
        r"ModalWorkItemTemplates\s+stages|Group\s*=\s*\{[^}]*stages|"
        r"evolutionStages|evo-stage-group|workflow-card|InterruptedWorkflow|"
        r"Projection: stage-|next stage",
        frontend_files,
    )
    assert not frontend_matches, _format_matches(frontend_matches)


def test_final_company_naming_cleanup_does_not_regress() -> None:
    """Keep final cleanup targets out of active code and renamed fixtures."""

    files = [
        REPO_ROOT / "opc/layer4_tools/collaboration.py",
        REPO_ROOT / "tests/test_company_collaboration.py",
        REPO_ROOT / "tests/test_engine_session_defaults.py",
        REPO_ROOT / "tests/test_prompt_assembly_dedup.py",
        REPO_ROOT / "tests/test_task_mode_contract.py",
        REPO_ROOT / "opc/plugins/office_ui/tests/test_event_adapter.py",
    ]
    matches = _scan(
        r"work-graph task metadata|same_role_stages|stage_budget|fixed_stages|"
        r"company_workflow|external_stage|StageIdentityDeduplicationTests|"
        r"test_full_company_workflow",
        files,
    )
    assert not matches, _format_matches(matches)


def test_final_literal_cleanup_has_no_old_company_runtime_tokens() -> None:
    files = (
        _production_python_files()
        + _ordinary_test_files()
        + list((REPO_ROOT / "opc/plugins/office_ui/tests").rglob("*.py"))
    )
    matches = _scan(
        r"runtime-graph|snapshot_builder_workflow_log|workflow_log|"
        r"workflow_definition_mode|workflow_projection_source",
        files,
    )
    assert not matches, _format_matches(matches)


def test_company_mode_file_names_are_work_item_based() -> None:
    matches: list[tuple[Path, int, str]] = []
    regex = re.compile(r"workflow_log|stage_b|company_workflow")
    for root in (REPO_ROOT / "opc", REPO_ROOT / "tests"):
        for path in root.rglob("*"):
            parts = set(path.parts)
            if "frontend_dist" in parts or "node_modules" in parts:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if regex.search(rel):
                matches.append((path, 0, rel))
    assert not matches, _format_matches(matches)


def test_ordinary_company_tests_use_work_item_fixture_names() -> None:
    """Keep ordinary tests projection-first."""

    matches = _scan(
        r"\bstage\b|\bStage\b|stage_|_stage|\bworkflow\b|\bWorkflow\b|workflow_|_workflow",
        _ordinary_test_files(),
    )
    assert not matches, _format_matches(matches)
