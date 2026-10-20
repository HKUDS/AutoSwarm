from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _scan(pattern: str, roots: list[Path]) -> list[str]:
    regex = re.compile(pattern)
    matches: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else list(root.rglob("*"))
        for path in paths:
            if path.is_dir() or path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            parts = set(path.parts)
            if "frontend_dist" in parts or "node_modules" in parts:
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {line.strip()}")
    return matches


def test_custom_market_and_ui_do_not_create_work_item_projection_title_packages() -> None:
    matches = _scan(
        r"work_item_projection_titles|projection_ids|stages_count|WorkflowDefinitionConfig|WorkflowStageConfig|WorkflowGateConfig|_auto_regen_workflow_if_needed",
        [
            REPO_ROOT / "opc/market",
            REPO_ROOT / "opc/plugins/office_ui/ws_handler.py",
            REPO_ROOT / "opc/plugins/office_ui/frontend_src",
        ],
    )
    assert not matches, "\n".join(matches)


def test_production_does_not_request_custom_workflow_graph_generation() -> None:
    matches = _scan(
        r"build_workflow_definition\(\s*[\"']custom[\"']",
        [
            REPO_ROOT / "opc/engine.py",
            REPO_ROOT / "opc/layer2_organization",
            REPO_ROOT / "opc/plugins/office_ui",
        ],
    )
    assert not matches, "\n".join(matches)


def test_company_runtime_does_not_import_legacy_workflow_plan_types() -> None:
    matches = _scan(
        r"\bCompanyWorkflowPlan\b|\bWorkflowStage\b|\bStageGate\b|\bStageGateType\b|workflow_plan",
        [
            REPO_ROOT / "opc/engine.py",
            REPO_ROOT / "opc/layer2_organization/company_mode.py",
            REPO_ROOT / "opc/layer2_organization/org_engine.py",
        ],
    )
    assert not matches, "\n".join(matches)


def test_company_runtime_does_not_use_work_item_plan_stage_bridge() -> None:
    matches = _scan(
        r"plan\.stages|stage\.stage_id|stage\.description|stage\.dependencies|stage\.gate\b|"
        r"getattr\(plan,\s*[\"']stages[\"']|"
        r"getattr\(raw_projection,\s*[\"'](stage_id|description|dependencies|gate)[\"']|"
        r"_coerce_work_item_assignment|_default_work_item_assignment|_task_effective_stage|_work_item_effective_stage|"
        r"source_stage_refs|delivery_stage_id",
        [
            REPO_ROOT / "opc/engine.py",
            REPO_ROOT / "opc/layer2_organization/company_mode.py",
            REPO_ROOT / "opc/layer2_organization/org_work_item_planner.py",
        ],
    )
    assert not matches, "\n".join(matches)


def test_work_item_projection_spec_exposes_only_projection_first_contract() -> None:
    matches = _scan(
        r"def\s+(stage_id|description|dependencies|gate|stages)\b",
        [REPO_ROOT / "opc/layer2_organization/org_work_item_planner.py"],
    )
    assert not matches, "\n".join(matches)
