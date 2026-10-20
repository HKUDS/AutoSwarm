"""Agent Store — dynamic character registry with flexible office assignment.

Each agent is an individual character/person with appearance, office assignment,
and a backing AutoSwarm role for execution. Persisted in SQLite.

Tables:
  agents              — active agent registry (cleared & rebuilt on preset switch)
  agent_appearances   — visual identity cache (survives mode switches)
  server_state        — persists exec_mode/company_profile across restarts
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite


# Default office assignment for preset models
_CLASSIC_LAYOUT: list[dict[str, Any]] = [
    {"role_id": "coordinator", "office_id": "office-0", "palette": 0, "seat_zone": "leaderOffice"},
    {"role_id": "executor",    "office_id": "office-1", "palette": 1, "seat_zone": "workspace"},
    {"role_id": "reviewer",    "office_id": "office-2", "palette": 2, "seat_zone": "workspace"},
]


class AgentStore:
    """Dynamic character registry with flexible office assignment.

    SQLite table: agents
      agent_id TEXT PK, name TEXT, description TEXT, opc_role_id TEXT,
      office_id TEXT, palette INT, hue_shift INT, seat_zone TEXT,
      specialties TEXT (JSON), status TEXT, created_at REAL
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db
        self._local_talent_cache: list[Any] | None = None

    async def initialize(self) -> None:
        """Create tables if not exists."""
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                opc_role_id TEXT NOT NULL,
                office_id TEXT DEFAULT 'office-0',
                palette INTEGER DEFAULT 0,
                hue_shift INTEGER DEFAULT 0,
                seat_zone TEXT DEFAULT 'workspace',
                desk_id TEXT DEFAULT NULL,
                specialties TEXT DEFAULT '[]',
                status TEXT DEFAULT 'idle',
                created_at REAL NOT NULL
            )
        """)
        # Appearance memory: persists visual identity across mode switches
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS agent_appearances (
                role_id TEXT PRIMARY KEY,
                palette INTEGER DEFAULT 0,
                hue_shift INTEGER DEFAULT 0,
                seat_zone TEXT DEFAULT 'workspace',
                office_id TEXT DEFAULT 'office-0',
                desk_id TEXT DEFAULT NULL
            )
        """)
        # Persistent server state (exec_mode, company_profile)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS server_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # Migration: add desk_id column if missing (existing DBs)
        try:
            await self._db.execute("SELECT desk_id FROM agents LIMIT 1")
        except Exception:
            await self._db.execute("ALTER TABLE agents ADD COLUMN desk_id TEXT DEFAULT NULL")
        # Migration: add employee_id column if missing (existing DBs)
        try:
            await self._db.execute("SELECT employee_id FROM agents LIMIT 1")
        except Exception:
            await self._db.execute("ALTER TABLE agents ADD COLUMN employee_id TEXT DEFAULT NULL")
        # Shadow copy of custom-mode agents: survives non-custom preset switches.
        # Written on every custom-mode agent mutation; read when re-entering custom.
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS custom_agents_shadow (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                opc_role_id TEXT NOT NULL,
                office_id TEXT DEFAULT 'office-0',
                palette INTEGER DEFAULT 0,
                hue_shift INTEGER DEFAULT 0,
                seat_zone TEXT DEFAULT 'workspace',
                desk_id TEXT DEFAULT NULL,
                employee_id TEXT DEFAULT NULL,
                specialties TEXT DEFAULT '[]',
                created_at REAL NOT NULL
            )
        """)
        # Migration: drop legacy table from earlier iteration
        await self._db.execute("DROP TABLE IF EXISTS custom_agents_backup")
        await self._db.commit()

    # ── Server state persistence ──────────────────────────────────────

    async def get_server_state(self, key: str, default: str = "") -> str:
        """Read a persisted server state value."""
        cursor = await self._db.execute(
            "SELECT value FROM server_state WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else default

    async def set_server_state(self, key: str, value: str) -> None:
        """Write a persisted server state value."""
        await self._db.execute(
            "INSERT OR REPLACE INTO server_state (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._db.commit()

    # ── Appearance persistence ────────────────────────────────────────

    async def _save_appearances(self) -> None:
        """Persist current agents' visual identity to agent_appearances table."""
        cursor = await self._db.execute(
            "SELECT agent_id, palette, hue_shift, seat_zone, office_id, desk_id FROM agents"
        )
        rows = await cursor.fetchall()
        for agent_id, palette, hue_shift, seat_zone, office_id, desk_id in rows:
            await self._db.execute(
                "INSERT OR REPLACE INTO agent_appearances "
                "(role_id, palette, hue_shift, seat_zone, office_id, desk_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (agent_id, palette, hue_shift, seat_zone, office_id, desk_id),
            )
        await self._db.commit()

    async def _restore_appearance(self, agent_id: str) -> dict[str, Any] | None:
        """Look up saved appearance for a role_id. Returns dict or None."""
        cursor = await self._db.execute(
            "SELECT palette, hue_shift, seat_zone, office_id, desk_id "
            "FROM agent_appearances WHERE role_id = ?",
            (agent_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "palette": row[0],
            "hue_shift": row[1],
            "seat_zone": row[2],
            "office_id": row[3],
            "desk_id": row[4],
        }

    # ── Custom-mode shadow copy ──────────────────────────────────────

    async def sync_custom_shadow(self) -> None:
        """Mirror the current agents table into custom_agents_shadow.

        Call this after any mutation to agents while in custom mode so the
        shadow is always up-to-date — even if the server crashes before a
        clean mode switch.
        """
        cursor = await self._db.execute(
            "SELECT agent_id, name, description, opc_role_id, office_id, "
            "palette, hue_shift, seat_zone, desk_id, employee_id, specialties, created_at "
            "FROM agents"
        )
        rows = await cursor.fetchall()
        await self._db.execute("DELETE FROM custom_agents_shadow")
        for row in rows:
            await self._db.execute(
                "INSERT INTO custom_agents_shadow "
                "(agent_id, name, description, opc_role_id, office_id, "
                "palette, hue_shift, seat_zone, desk_id, employee_id, specialties, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        await self._db.commit()

    async def _restore_custom_shadow(self) -> list[dict[str, Any]]:
        """Restore agents from the shadow table. Returns agents or []."""
        cursor = await self._db.execute(
            "SELECT agent_id, name, description, opc_role_id, office_id, "
            "palette, hue_shift, seat_zone, desk_id, employee_id, specialties, created_at "
            "FROM custom_agents_shadow"
        )
        rows = await cursor.fetchall()
        if not rows:
            return []
        await self._db.execute("DELETE FROM agents")
        for row in rows:
            await self._db.execute(
                "INSERT OR REPLACE INTO agents "
                "(agent_id, name, description, opc_role_id, office_id, "
                "palette, hue_shift, seat_zone, desk_id, employee_id, specialties, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?)",
                row,
            )
        await self._db.commit()
        return await self.get_all()

    # ── Preset loading ────────────────────────────────────────────────

    async def load_preset(self, model_name: str, org_engine: Any) -> list[dict[str, Any]]:
        """Load agents from a preset runtime model.

        Returns list of agent dicts in UI AgentInfo format.
        Supported presets: corporate, single, custom.

        Custom mode: restore the saved custom team when available. If there
        is no saved custom team yet, clear whatever preset agents are active
        and start from a single custom leader.
        Non-custom: save appearances, clear, rebuild from preset definition.
        """
        if model_name == "custom":
            # Shadow is authoritative when it exists because the agents
            # table may contain leftover preset agents from a non-custom mode.
            restored = await self._restore_custom_shadow()
            if restored:
                return restored
            # No saved custom team yet — never inherit preset agents.
            await self._save_appearances()
            await self._db.execute("DELETE FROM agents")
            await self._db.commit()
            starter = await self._load_custom_starter(org_engine)
            reconciled = await self.ensure_custom_role_agents(org_engine)
            if reconciled:
                return reconciled
            await self.sync_custom_shadow()
            return starter

        # Non-custom preset: save appearances, clear, rebuild.
        await self._save_appearances()
        await self._db.execute("DELETE FROM agents")

        # Single mode: only the executor
        if model_name == "single":
            return await self._load_single_agent(org_engine)

        if model_name == "corporate":
            try:
                from opc.layer2_organization.company_runtime_profiles import get_builtin_roles
                config = getattr(org_engine, "config", None)
                org = getattr(config, "org", None)
                configured_roles = getattr(org, "roles", None)
                role_configs = get_builtin_roles(model_name, configured_roles=configured_roles)
                roles = [
                    {"id": r.id, "name": r.name, "responsibility": r.responsibility or "",
                     "tools": list(r.tools) if r.tools else [],
                     "can_spawn": list(r.can_spawn) if r.can_spawn else [],
                     "reports_to": r.reports_to or ""}
                    for r in role_configs
                ]
            except ImportError:
                roles = []
        else:
            roles = []

        if not roles:
            # Use current org_engine roles as fallback
            roles_info = org_engine.list_agents() if hasattr(org_engine, "list_agents") else []
            if not roles_info:
                # Fallback to hardcoded 3-agent layout
                return await self._load_classic_fallback(org_engine)
            return await self._load_from_role_infos(roles_info, model_name)

        return await self._load_from_role_dicts(roles, model_name)

    async def _load_single_agent(self, org_engine: Any) -> list[dict[str, Any]]:
        """Single-agent mode: only one executor in office-0."""
        role = None
        if hasattr(org_engine, "get_agent"):
            role = org_engine.get_agent("executor")
        name = role.name if role else "Executor"
        description = role.responsibility if role else ""
        specialties = list(role.tools) if role and role.tools else []
        agent = await self._insert_agent(
            agent_id="executor",
            name=name,
            description=description,
            opc_role_id="executor",
            office_id="office-0",
            palette=1,
            seat_zone="leaderOffice",
            specialties=specialties,
        )
        return [agent]

    async def _load_custom_starter(self, org_engine: Any) -> list[dict[str, Any]]:
        """Custom mode first-time bootstrap: 1 agent in leader desk."""
        role = None
        if hasattr(org_engine, "get_agent"):
            role = org_engine.get_agent("coordinator")
            if not role:
                role = org_engine.get_agent("executor")
        name = role.name if role else "My Agent"
        description = role.responsibility if role else "Custom agent"
        specialties = list(role.tools) if role and role.tools else []
        agent = await self._insert_agent(
            agent_id="custom-leader",
            name=name,
            description=description,
            opc_role_id="coordinator",
            office_id="office-0",
            palette=0,
            seat_zone="leaderOffice",
            specialties=specialties,
            restore_appearance=False,
        )
        return [agent]

    async def ensure_custom_role_agents(self, org_engine: Any) -> list[dict[str, Any]]:
        """Make custom-mode visual agents match the active org roles."""
        if not org_engine or not hasattr(org_engine, "list_agents"):
            return await self.get_all()

        roles = list(org_engine.list_agents() or [])
        if not roles:
            return await self.get_all()

        role_ids = [self._role_id(role) for role in roles]
        role_ids = [role_id for role_id in role_ids if role_id]
        desired_role_ids = set(role_ids)
        if not desired_role_ids:
            return await self.get_all()

        selected_by_role: dict[str, Any] = {}
        for role_id in role_ids:
            employees = []
            if hasattr(org_engine, "list_employees"):
                employees = list(org_engine.list_employees(role_id=role_id) or [])
            if not employees and hasattr(org_engine, "ensure_default_employee_for_role"):
                default_employee = org_engine.ensure_default_employee_for_role(role_id, persist=False)
                if default_employee is not None:
                    employees = [default_employee]
            if employees:
                selected_by_role[role_id] = self._select_role_employee(employees)

        desired_employee_ids = {
            getattr(employee, "employee_id", "")
            for employee in selected_by_role.values()
            if getattr(employee, "employee_id", "")
        }

        existing_agents = await self.get_all()
        for agent in existing_agents:
            role_id = str(agent.get("opc_role_id") or agent.get("agent_id") or "")
            employee_id = str(agent.get("employee_id") or "")
            if role_id not in desired_role_ids:
                await self.remove_agent(str(agent.get("agent_id") or ""))
                continue
            selected = selected_by_role.get(role_id)
            selected_employee_id = str(getattr(selected, "employee_id", "") or "")
            if selected_employee_id and employee_id and employee_id != selected_employee_id:
                await self.remove_agent(str(agent.get("agent_id") or ""))
            elif selected_employee_id and not employee_id:
                await self.remove_agent(str(agent.get("agent_id") or ""))

        offices = ["office-0", "office-1", "office-2"]
        worker_idx = 0
        for index, role in enumerate(roles):
            role_id = self._role_id(role)
            employee = selected_by_role.get(role_id)
            employee_id = str(getattr(employee, "employee_id", "") or "")
            if not employee_id:
                continue
            if await self._find_by_employee_id(employee_id):
                continue
            is_leader = self._is_leader_role(role, index)
            office_id = "office-0" if is_leader else offices[1 + (worker_idx % 2)]
            seat_zone = "leaderOffice" if is_leader else "workspace"
            if not is_leader:
                worker_idx += 1
            await self.create_agent_from_employee(
                self._employee_to_dict(employee),
                office_id=office_id,
                seat_zone=seat_zone,
            )

        for agent in await self.get_all():
            employee_id = str(agent.get("employee_id") or "")
            if employee_id and employee_id not in desired_employee_ids:
                await self.remove_agent(str(agent.get("agent_id") or ""))

        await self.sync_custom_shadow()
        return await self.get_all()

    @staticmethod
    def _role_id(role: Any) -> str:
        return str(getattr(role, "role_id", "") or getattr(role, "id", "") or "").strip()

    @staticmethod
    def _is_leader_role(role: Any, index: int) -> bool:
        can_spawn = getattr(role, "can_spawn", None) or []
        reports_to = getattr(role, "reports_to", None) or ""
        return index == 0 or (bool(can_spawn) and reports_to in ("", "owner"))

    @staticmethod
    def _select_role_employee(employees: list[Any]) -> Any:
        for employee in employees:
            metadata = dict(getattr(employee, "metadata", {}) or {})
            if not metadata.get("is_default_employee"):
                return employee
        return employees[0]

    @staticmethod
    def _employee_to_dict(employee: Any) -> dict[str, Any]:
        return {
            "employee_id": getattr(employee, "employee_id", ""),
            "name": getattr(employee, "name", ""),
            "role_id": getattr(employee, "role_id", ""),
            "category": getattr(employee, "category", ""),
            "domains": list(getattr(employee, "domains", []) or []),
            "tags": list(getattr(employee, "tags", []) or []),
        }

    async def _load_classic_fallback(self, org_engine: Any) -> list[dict[str, Any]]:
        """Fallback: create classic agents from hardcoded layout."""
        agents = []
        for layout in _CLASSIC_LAYOUT:
            role_id = layout["role_id"]
            # Try to get role info from org_engine
            role = None
            if hasattr(org_engine, "get_agent"):
                role = org_engine.get_agent(role_id)

            name = role.name if role else role_id.capitalize()
            description = role.responsibility if role else ""
            specialties = list(role.tools) if role and role.tools else []

            agent = await self._insert_agent(
                agent_id=role_id,
                name=name,
                description=description,
                opc_role_id=role_id,
                office_id=layout["office_id"],
                palette=layout["palette"],
                seat_zone=layout["seat_zone"],
                specialties=specialties,
            )
            agents.append(agent)
        return agents

    async def _load_from_role_infos(self, roles: list[Any], model_name: str) -> list[dict[str, Any]]:
        """Load from AutoSwarm AgentInfo objects.

        Groups agents into offices: leaders in office-0, workers spread across office-1/2.
        """
        agents = []
        offices = ["office-0", "office-1", "office-2"]
        worker_idx = 0

        for i, role in enumerate(roles):
            role_id = role.role_id if hasattr(role, "role_id") else str(role)
            name = role.name if hasattr(role, "name") else role_id
            description = role.responsibility if hasattr(role, "responsibility") else ""
            specialties = list(role.tools) if hasattr(role, "tools") and role.tools else []

            # Leaders go to office-0: first agent, or agents that report to
            # "owner"/nobody (top of hierarchy)
            can_spawn = getattr(role, "can_spawn", None) or []
            reports_to = getattr(role, "reports_to", None) or ""
            is_leader = i == 0 or (bool(can_spawn) and reports_to in ("", "owner"))

            if is_leader:
                office_id = "office-0"
                seat_zone = "leaderOffice"
            else:
                # Workers spread across office-1 and office-2
                office_id = offices[1 + (worker_idx % 2)]
                seat_zone = "workspace"
                worker_idx += 1

            agent = await self._insert_agent(
                agent_id=role_id,
                name=name,
                description=description,
                opc_role_id=role_id,
                office_id=office_id,
                palette=i % 6,
                seat_zone=seat_zone,
                specialties=specialties,
            )
            agents.append(agent)
        return agents

    # Office layout maps for preset profiles
    _OFFICE_MAPS: dict[str, dict[str, str]] = {
        "corporate": {
            "ceo": "office-0",
            "cto": "office-1", "senior_engineer": "office-1", "devops_engineer": "office-1",
            "cmo": "office-2", "coo": "office-2",
            "content_specialist": "office-2", "designer": "office-2", "qa_analyst": "office-2",
        },
    }
    _LEADER_SETS: dict[str, set[str]] = {
        "corporate": {"ceo"},
    }

    async def _load_from_role_dicts(self, roles: list[dict[str, Any]], model_name: str) -> list[dict[str, Any]]:
        """Load from role config dicts.

        Groups agents into offices using profile-specific layout maps:
          corporate: office-0 CEO, office-1 Engineering, office-2 Business/Ops
        """
        office_map = self._OFFICE_MAPS.get(model_name, {})
        leaders = self._LEADER_SETS.get(model_name, set())
        offices = ["office-0", "office-1", "office-2"]

        agents = []
        for i, role_dict in enumerate(roles):
            role_id = role_dict.get("id", f"role-{i}")
            name = role_dict.get("name", role_id)
            description = role_dict.get("responsibility", "")
            specialties = role_dict.get("tools", [])

            office_id = office_map.get(role_id, offices[i % len(offices)])
            seat_zone = "leaderOffice" if role_id in leaders else "workspace"

            agent = await self._insert_agent(
                agent_id=role_id,
                name=name,
                description=description,
                opc_role_id=role_id,
                office_id=office_id,
                palette=i % 6,
                seat_zone=seat_zone,
                specialties=specialties,
            )
            agents.append(agent)
        return agents

    async def _insert_agent(
        self,
        agent_id: str,
        name: str,
        description: str,
        opc_role_id: str,
        office_id: str,
        palette: int,
        seat_zone: str,
        specialties: list[str] | None = None,
        hue_shift: int = 0,
        desk_id: str | None = None,
        employee_id: str | None = None,
        restore_appearance: bool = True,
    ) -> dict[str, Any]:
        """Insert a single agent and return its UI AgentInfo dict.

        If restore_appearance is True, checks agent_appearances table for
        previously saved visual identity and uses it instead of defaults.
        """
        # Restore saved appearance if available
        if restore_appearance:
            saved = await self._restore_appearance(agent_id)
            if saved:
                palette = saved["palette"]
                hue_shift = saved["hue_shift"]
                seat_zone = saved["seat_zone"]
                office_id = saved["office_id"]
                desk_id = saved["desk_id"]

        now = time.time()
        specs_json = json.dumps(specialties or [])
        await self._db.execute(
            "INSERT OR REPLACE INTO agents "
            "(agent_id, name, description, opc_role_id, office_id, palette, hue_shift, seat_zone, desk_id, employee_id, specialties, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?)",
            (agent_id, name, description, opc_role_id, office_id, palette, hue_shift, seat_zone, desk_id, employee_id, specs_json, now),
        )
        await self._db.commit()
        return self._to_ui_format(
            agent_id, name, description, specialties or [], "idle", palette, hue_shift, seat_zone, office_id, desk_id, employee_id, opc_role_id
        )

    async def create_agent(
        self,
        name: str,
        opc_role_id: str,
        office_id: str,
        org_engine: Any = None,
        description: str = "",
        specialties: list[str] | None = None,
        tools: list[str] | None = None,
        palette: int | None = None,
        seat_zone: str = "workspace",
        employee_id: str | None = None,
    ) -> dict[str, Any]:
        """User creates a new character in the UI.

        Accepts optional custom fields. Falls back to org_engine role info
        for anything not explicitly provided.
        """
        # Merge with org_engine role info (use provided values first)
        if org_engine and hasattr(org_engine, "get_agent"):
            role = org_engine.get_agent(opc_role_id)
            if role:
                if not description:
                    description = role.responsibility or ""
                if not specialties and not tools:
                    specialties = list(role.tools) if role.tools else []

        # Merge tools into specialties for display
        final_specialties = list(specialties or [])
        if tools:
            for t in tools:
                if t not in final_specialties:
                    final_specialties.append(t)

        # Auto-assign palette if not specified
        if palette is None:
            count = await self._count()
            palette = count % 6

        # Generate unique agent_id
        agent_id = f"{opc_role_id}-{name.lower().replace(' ', '-')}"
        # Ensure uniqueness
        existing = await self._get_one(agent_id)
        if existing:
            agent_id = f"{agent_id}-{int(time.time()) % 10000}"

        return await self._insert_agent(
            agent_id=agent_id,
            name=name,
            description=description,
            opc_role_id=opc_role_id,
            office_id=office_id,
            palette=palette,
            seat_zone=seat_zone,
            specialties=final_specialties,
            employee_id=employee_id,
            restore_appearance=False,  # User-created agents: use explicit values
        )

    async def remove_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Remove a character. Returns the agent dict for broadcasting."""
        agent = await self._get_one(agent_id)
        if not agent:
            return None
        await self._db.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        await self._db.commit()
        return agent

    async def move_agent(self, agent_id: str, office_id: str, seat_zone: str | None = None, desk_id: str | None = None) -> dict[str, Any] | None:
        """Move character to different office/desk. Purely visual."""
        fields = ["office_id = ?"]
        params: list[Any] = [office_id]
        if seat_zone:
            fields.append("seat_zone = ?")
            params.append(seat_zone)
        if desk_id is not None:
            fields.append("desk_id = ?")
            params.append(desk_id)
        params.append(agent_id)
        await self._db.execute(
            f"UPDATE agents SET {', '.join(fields)} WHERE agent_id = ?",
            tuple(params),
        )
        await self._db.commit()
        return await self._get_one(agent_id)

    async def update_status(self, agent_id: str, status: str) -> None:
        """Update character status from AutoSwarm events."""
        if not agent_id:
            return
        await self._db.execute(
            "UPDATE agents SET status = ? WHERE agent_id = ?",
            (status, agent_id),
        )
        await self._db.commit()

    async def get_all(self) -> list[dict[str, Any]]:
        """Return all agents in the EXACT format UI AgentInfo expects."""
        cursor = await self._db.execute(
            "SELECT agent_id, name, description, specialties, status, "
            "palette, hue_shift, seat_zone, office_id, desk_id, employee_id, opc_role_id FROM agents ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        agents = []
        for row in rows:
            agent_id, name, description, specs_json, status, palette, hue_shift, seat_zone, office_id, desk_id, employee_id, opc_role_id = row
            specialties = json.loads(specs_json) if specs_json else []
            agents.append(self._to_ui_format(
                agent_id, name, description, specialties, status, palette, hue_shift, seat_zone, office_id, desk_id, employee_id, opc_role_id
            ))
        return agents

    async def get_templates(self, org_engine: Any = None) -> list[dict[str, Any]]:
        """Generate agent templates for UI create-agent dialog.

        Reads from AutoSwarm org_engine to get available roles.
        """
        if not org_engine or not hasattr(org_engine, "list_agents"):
            return []

        templates = []
        icon_map = {
            "coordinator": "compass",
            "executor": "hammer",
            "reviewer": "magnifying-glass",
        }
        roles = org_engine.list_agents()
        for role in roles:
            role_id = role.role_id if hasattr(role, "role_id") else str(role)
            name = role.name if hasattr(role, "name") else role_id
            desc = role.responsibility if hasattr(role, "responsibility") else ""
            tools = list(role.tools) if hasattr(role, "tools") and role.tools else []
            templates.append({
                "id": role_id,
                "label": name,
                "icon": icon_map.get(role_id, "person"),
                "desc": desc[:60],
                "tools": tools,
            })
        return templates

    # ── Employee → Agent bridge ──────────────────────────────────────────

    async def create_agent_from_employee(
        self, employee: dict[str, Any], office_id: str = "office-0", seat_zone: str = "workspace",
    ) -> dict[str, Any]:
        """Create a visual agent linked to an org employee (idempotent)."""
        emp_id = employee.get("employee_id", "")
        existing = await self._find_by_employee_id(emp_id)
        if existing:
            return existing

        agent_id = f"emp-{emp_id}"
        name = employee.get("name", emp_id)
        description = employee.get("category", "")
        opc_role_id = employee.get("role_id", "executor")
        domains = list(employee.get("domains", []))
        tags = list(employee.get("tags", []))
        specialties = domains + [t for t in tags if t not in domains]

        count = await self._count()
        palette = count % 6

        return await self._insert_agent(
            agent_id=agent_id,
            name=name,
            description=description,
            opc_role_id=opc_role_id,
            office_id=office_id,
            palette=palette,
            seat_zone=seat_zone,
            specialties=specialties,
            employee_id=emp_id,
            restore_appearance=False,
        )

    async def _find_by_employee_id(self, employee_id: str) -> dict[str, Any] | None:
        """Find an agent linked to a given employee_id."""
        cursor = await self._db.execute(
            "SELECT agent_id, name, description, specialties, status, "
            "palette, hue_shift, seat_zone, office_id, desk_id, employee_id, opc_role_id "
            "FROM agents WHERE employee_id = ?",
            (employee_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        agent_id, name, description, specs_json, status, palette, hue_shift, seat_zone, office_id, desk_id, eid, opc_role_id = row
        specialties = json.loads(specs_json) if specs_json else []
        return self._to_ui_format(agent_id, name, description, specialties, status, palette, hue_shift, seat_zone, office_id, desk_id, eid, opc_role_id)

    async def get_employee_agent_map(self) -> dict[str, str]:
        """Return {employee_id: agent_id} for all employee-linked agents."""
        cursor = await self._db.execute(
            "SELECT employee_id, agent_id FROM agents WHERE employee_id IS NOT NULL"
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_role_agent_map(self) -> dict[str, str]:
        """Return {opc_role_id: agent_id} for all agents.

        Used by EventAdapter to resolve AutoSwarm role_id → UI agent_id.
        If multiple agents share a role, last one wins (acceptable for status routing).
        """
        cursor = await self._db.execute(
            "SELECT opc_role_id, agent_id FROM agents"
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def update_status_by_role(self, role_id: str, status: str) -> None:
        """Update status for all agents with the given opc_role_id."""
        if not role_id:
            return
        await self._db.execute(
            "UPDATE agents SET status = ? WHERE opc_role_id = ?",
            (status, role_id),
        )
        await self._db.commit()

    # ── Private helpers ────────────────────────────────────────────────────

    async def _get_one(self, agent_id: str) -> dict[str, Any] | None:
        cursor = await self._db.execute(
            "SELECT agent_id, name, description, specialties, status, "
            "palette, hue_shift, seat_zone, office_id, desk_id, employee_id, opc_role_id FROM agents WHERE agent_id = ?",
            (agent_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        agent_id, name, description, specs_json, status, palette, hue_shift, seat_zone, office_id, desk_id, employee_id, opc_role_id = row
        specialties = json.loads(specs_json) if specs_json else []
        return self._to_ui_format(agent_id, name, description, specialties, status, palette, hue_shift, seat_zone, office_id, desk_id, employee_id, opc_role_id)

    async def _count(self) -> int:
        cursor = await self._db.execute("SELECT COUNT(*) FROM agents")
        row = await cursor.fetchone()
        return row[0] if row else 0

    @staticmethod
    def _to_ui_format(
        agent_id: str,
        name: str,
        description: str,
        specialties: list[str],
        status: str,
        palette: int,
        hue_shift: int,
        seat_zone: str,
        office_id: str = "office-0",
        desk_id: str | None = None,
        employee_id: str | None = None,
        opc_role_id: str | None = None,
    ) -> dict[str, Any]:
        """Convert to the EXACT format frontend AgentInfo expects."""
        appearance: dict[str, Any] = {
            "palette": palette,
            "hue_shift": hue_shift,
            "seat_zone": seat_zone,
        }
        if desk_id:
            appearance["desk_id"] = desk_id
        result: dict[str, Any] = {
            "agent_id": agent_id,
            "name": name,
            "description": description,
            "specialties": specialties,
            "status": status,
            "office_id": office_id,
            "appearance": appearance,
            "opc_role_id": opc_role_id or agent_id,
        }
        if employee_id:
            result["employee_id"] = employee_id
        return result
