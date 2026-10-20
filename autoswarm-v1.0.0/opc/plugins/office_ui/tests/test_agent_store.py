from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace

import aiosqlite

from opc.core.config import RoleConfig
from opc.plugins.office_ui.agent_store import AgentStore


@dataclass
class _Role:
    name: str
    responsibility: str = ""
    tools: list[str] = field(default_factory=list)


class _OrgEngine:
    def get_agent(self, role_id: str) -> _Role | None:
        roles = {
            "coordinator": _Role(
                name="Custom Leader",
                responsibility="Lead the custom team.",
                tools=["send_dm", "todo_write"],
            ),
            "executor": _Role(
                name="Executor",
                responsibility="Execute assigned work.",
                tools=["shell_exec"],
            ),
        }
        return roles.get(role_id)


class _PresetOrgEngine(_OrgEngine):
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            org=SimpleNamespace(
                roles=[
                    RoleConfig(
                        id="ceo",
                        name="Configured CEO",
                        responsibility="Configured leader",
                        tools=["file_read", "todo_write"],
                    )
                ]
            )
        )


class AgentStoreCustomModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.db = await aiosqlite.connect(":memory:")
        self.store = AgentStore(self.db)
        await self.store.initialize()
        self.org_engine = _OrgEngine()

    async def asyncTearDown(self) -> None:
        await self.db.close()

    async def test_custom_mode_without_shadow_resets_to_single_starter(self) -> None:
        preset_agents = await self.store.load_preset("corporate", self.org_engine)
        self.assertGreater(len(preset_agents), 1)
        self.assertTrue(any(agent["opc_role_id"] == "ceo" for agent in preset_agents))

        custom_agents = await self.store.load_preset("custom", self.org_engine)

        self.assertEqual(len(custom_agents), 1)
        self.assertEqual(custom_agents[0]["agent_id"], "custom-leader")
        self.assertEqual(custom_agents[0]["opc_role_id"], "coordinator")
        self.assertFalse(any(agent["opc_role_id"] == "ceo" for agent in custom_agents))

    async def test_custom_mode_restores_saved_custom_team(self) -> None:
        starter_agents = await self.store.load_preset("custom", self.org_engine)
        self.assertEqual([agent["agent_id"] for agent in starter_agents], ["custom-leader"])

        await self.store.create_agent(
            name="Planner",
            opc_role_id="planner",
            office_id="office-1",
            description="Plans the work.",
            specialties=["planning"],
        )
        await self.store.sync_custom_shadow()

        await self.store.load_preset("corporate", self.org_engine)
        restored_agents = await self.store.load_preset("custom", self.org_engine)
        restored_role_ids = {agent["opc_role_id"] for agent in restored_agents}

        self.assertEqual(restored_role_ids, {"coordinator", "planner"})
        self.assertFalse(any(agent["opc_role_id"] == "ceo" for agent in restored_agents))

    async def test_builtin_preset_uses_configured_role_tool_overrides(self) -> None:
        preset_agents = await self.store.load_preset("corporate", _PresetOrgEngine())
        ceo = next(agent for agent in preset_agents if agent["opc_role_id"] == "ceo")

        self.assertEqual(ceo["name"], "Configured CEO")
        self.assertEqual(set(ceo["specialties"]), {"file_read", "todo_write"})

