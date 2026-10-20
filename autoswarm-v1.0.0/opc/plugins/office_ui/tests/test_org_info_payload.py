from __future__ import annotations

from types import SimpleNamespace
import unittest

from opc.core.models import (
    DelegationCell,
    DelegationRun,
    DelegationRoleSession,
    DelegationWorkItem,
    Phase,
)
from opc.plugins.office_ui.ws_handler import WSHandler


class DummyStore:
    is_ready = True

    async def list_open_delegation_runs(self, project_id: str):  # noqa: ARG002
        return [
            DelegationRun(
                run_id="run-1",
                project_id=project_id,
                status="running",
                lifecycle_status="active",
                current_revision=3,
                latest_deliverable_summary="Latest delivery snapshot",
                recovery_pointer={"status": "warm"},
            )
        ]

    async def list_delegation_cells(self, run_id: str):
        return [
            DelegationCell(
                cell_id="cell-1",
                run_id=run_id,
                manager_role_id="role-1",
                member_role_ids=["role-1"],
                status="running",
                metadata={"is_final_decider_cell": True},
            )
        ]

    async def list_delegation_role_sessions(self, run_id: str):
        return [
            DelegationRoleSession(
                role_session_id="seat-1",
                run_id=run_id,
                role_id="role-1",
                employee_id="emp-1",
                focused_work_item_id="wi-1",
                background_work_item_ids=["wi-2"],
                manager_role_ids=["role-manager"],
                status="active",
            )
        ]

    async def list_delegation_work_items(self, run_id: str):
        return [
            DelegationWorkItem(
                work_item_id="wi-1",
                run_id=run_id,
                cell_id="cell-1",
                role_id="role-1",
                title="Primary work item",
                kind="execute",
                phase=Phase.RUNNING,
                batch_id="batch-1",
                batch_index=0,
                projection_id="projection-legacy",
                metadata={
                    "adaptive": {
                        "normalized_state": "waiting_for_gate",
                        "blocked_reason": "Waiting for required signals: implementation_ready",
                    }
                },
            ),
            DelegationWorkItem(
                work_item_id="wi-2",
                run_id=run_id,
                cell_id="cell-1",
                role_id="role-1",
                title="Follow-up work item",
                kind="execute",
                phase=Phase.APPROVED,
                batch_id="batch-1",
                batch_index=1,
                projection_id="projection-legacy",
            ),
        ]

    async def list_team_instances(self, run_id: str):  # noqa: ARG002
        return []

    async def list_seat_states(self, run_id: str):  # noqa: ARG002
        return []

    async def get_session_links(self, session_id: str, limit: int = 50):  # noqa: ARG002
        return []


class DummyOrg:
    def list_agents(self):
        return [
            SimpleNamespace(
                role_id="role-1",
                name="Role One",
                responsibility="Do the thing",
                status="active",
                reports_to="owner",
                icon=None,
                can_spawn=[],
                tools=["tool-a"],
                runtime_policy={"execution_strategy": "auto"},
                preferred_external_agent=None,
                prompt_refs=[],
            )
        ]

    def list_employees(self):
        return [
            SimpleNamespace(
                employee_id="emp-1",
                name="Employee One",
                role_id="role-1",
                category="general",
                domains=["ops"],
                seniority="senior",
                status="active",
                tags=["tag-1"],
                skill_refs=["skill-1"],
            )
        ]

    def get_company_profile(self):
        return "corporate"

    def get_execution_model(self):
        return "actor_runtime"

    def get_final_decider_role_id(self, strict: bool = False):  # noqa: ARG002
        return "role-1"

    def get_top_level_role_ids(self):
        return ["role-1"]

    def current_org_version(self):
        return 9

    def current_runtime_topology_version(self):
        return 4

    def get_runtime_policy(self, profile: str):  # noqa: ARG002
        return {"parallel": {"auto_dispatch": True}}


class DummyChannelManager:
    def get_all_statuses(self):
        return [
            {
                "name": "slack",
                "enabled": True,
                "running": True,
                "configured": True,
                "available": True,
                "ready": True,
                "last_error": None,
                "delivery_mode": "push",
            }
        ]


class DummyPackage:
    def model_dump(self):
        return {"package_id": "pkg-1", "name": "Package 1"}


class DummyEngine:
    def __init__(self):
        self.project_id = "project-1"
        self.org_engine = DummyOrg()
        self.store = DummyStore()
        self.channel_manager = DummyChannelManager()
        self.config = SimpleNamespace(
            org=SimpleNamespace(installed_packages=[DummyPackage()]),
            save=lambda: None,
        )
        self.on_company_runtime_children = None
        self.on_escalation = None
        self.escalation = None


class DummyStoreLike:
    pass


class TestOrgInfoPayload(unittest.IsolatedAsyncioTestCase):
    async def test_org_info_payload_exposes_modern_runtime_fields_only(self) -> None:
        handler = WSHandler(DummyEngine(), DummyStoreLike(), DummyStoreLike(), DummyStoreLike())

        payload = await handler._build_org_info_payload()

        self.assertNotIn("active_cells", payload)
        self.assertNotIn("role_sessions", payload)
        self.assertNotIn("active_work_items", payload)
        self.assertNotIn("run_frontier_summary", payload)
        self.assertNotIn("legacy_snapshot", payload)
        self.assertNotIn("execution_model", payload)
        self.assertEqual(payload["runtime_teams"][0]["cell_id"], "cell-1")
        self.assertEqual(payload["runtime_seats"][0]["role_session_id"], "seat-1")
        self.assertEqual(payload["project_run"]["run_id"], "run-1")
        self.assertEqual(payload["project_run"]["current_revision"], 3)
        self.assertEqual(payload["project_run"]["latest_deliverable_summary"], "Latest delivery snapshot")
        legacy_prefix = "work" + "flow_"
        self.assertNotIn(legacy_prefix + "definition_mode", payload)
        self.assertNotIn(legacy_prefix + "projection_source", payload)
        self.assertNotIn("work_item_projection_titles", payload)
        self.assertEqual(payload["company_profile"], "corporate")
        self.assertEqual(payload["runtime_topology_version"], 4)
        self.assertEqual(payload["runtime_policy"]["parallel"]["auto_dispatch"], True)

        self.assertEqual(payload["frontier"]["status"], "running")
        self.assertEqual(payload["work_items"][0]["batch_id"], "batch-1")
        self.assertEqual(payload["work_items"][0]["work_item_projection_id"], "projection-legacy")
        self.assertNotIn("projection_id", payload["work_items"][0])
        self.assertEqual(payload["work_items"][0]["adaptive"]["normalized_state"], "waiting_for_gate")
        self.assertEqual(payload["channels"][0]["name"], "slack")
        self.assertEqual(payload["installed_packages"][0]["package_id"], "pkg-1")
