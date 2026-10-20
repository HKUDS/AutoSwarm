"""Regression tests for the corporate-config WS import/export handlers.

Two related bugs were found in Apr 2026 after commit 47cea6f added
org_config import/export:

1. ``_handle_org_config_import`` built its merged dict with the uploaded
   ``company`` / ``roles`` / ``employees`` / ``escalation_rules`` at the
   **top** level. But OPCConfig nests those inside ``.org`` — Pydantic
   silently dropped the top-level keys (``extra='ignore'``), so
   ``validated_config`` was identical to the existing config and every
   import was effectively a no-op in memory. The disk write via
   ``raw_yaml`` looked like it worked, but the next ``config.save()``
   overwrote disk with the stale in-memory state.

2. ``OPCConfig.load()`` used to gate the entire ``org`` mapping on
   ``"company" in merged or "roles" in merged``. Runtime extras still
   need to load, but talent templates no longer live in org config.

This module pins both regressions.
"""

from __future__ import annotations

import tempfile
import unittest
import yaml as _yaml
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from opc.core.config import OPCConfig


class OrgConfigLoadDropsLegacyTalentWithMinimalCorporate(unittest.TestCase):
    """Talent templates are ignored from legacy org runtime files."""

    def test_minimal_corporate_preserves_talent_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "org_config.yaml").write_text(_yaml.dump({
                "talent_templates": [
                    {"id": "t1", "name": "Tester", "category": "qa", "description": "x"}
                ],
                "teams": [],
                "team_runtime": {"shared_role_session_scope": "team"},
                "installed_packages": [],
            }))
            # Corporate file exists but has NEITHER ``company`` nor ``roles``
            # — mirroring the state that triggered the bug.
            (tmp_path / "company_corporate_config.yaml").write_text(_yaml.dump({
                "schema_version": 1,
                "employees": [],
            }))
            cfg = OPCConfig.load(tmp_path)
            self.assertEqual(cfg.org.talent_templates, [])
            self.assertEqual(cfg.org.team_runtime.shared_role_session_scope, "team")

    def test_only_org_config_file_preserves_talent(self) -> None:
        """If company_corporate_config.yaml is entirely missing, org_config's
        fields must still reach OPCConfig.org."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "org_config.yaml").write_text(_yaml.dump({
                "talent_templates": [
                    {"id": "t2", "name": "Another", "category": "eng", "description": "x"}
                ],
            }))
            cfg = OPCConfig.load(tmp_path)
            self.assertEqual(cfg.org.talent_templates, [])


class WsImportHandlerActuallyAppliesUploadedRoles(unittest.IsolatedAsyncioTestCase):
    """Regression: the WS import handler must translate the uploaded
    corporate YAML (flat top-level company/roles/...) into the nested
    OPCConfig.org structure before validation, otherwise every import
    is silently dropped in memory."""

    async def test_import_apply_overlays_roles_onto_org(self) -> None:
        # Build a WS handler stub — we only exercise the one method.
        from opc.plugins.office_ui.ws_handler import WSHandler

        existing = OPCConfig()
        existing.org.company_name = "Old Co"
        existing.org.roles = []

        handler = WSHandler.__new__(WSHandler)
        handler.engine = SimpleNamespace(
            config=existing,
            org_engine=MagicMock(),
        )
        handler._config_lock = AsyncMock()
        handler._config_lock.__aenter__ = AsyncMock(return_value=None)
        handler._config_lock.__aexit__ = AsyncMock(return_value=None)
        handler._broadcast_org_info = AsyncMock()

        uploaded = {
            "schema_version": 1,
            "company": {
                "name": "New Co",
                "topology": "Flat",
                "company_profile": "custom",
                "execution_model": "recursive_delegation",
                "final_decider_role_id": "ceo",
                "company_profiles": ["custom"],
            },
            "roles": [
                {
                    "id": "ceo",
                    "name": "CEO",
                    "responsibility": "Lead",
                    "reports_to": "owner",
                }
            ],
            "employees": [],
            "escalation_rules": [],
        }
        raw_yaml = _yaml.dump(uploaded)

        ws = AsyncMock()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "config").mkdir()
            handler.engine.opc_home = tmp_path / ".opc"
            # Stub _find_project_root to point here so the handler writes
            # active split config files into a temp dir.
            with patch(
                "opc.core.config._find_project_root",
                return_value=tmp_path,
            ):
                await handler._handle_org_config_import(
                    ws, {"yaml": raw_yaml, "dry_run": False}
                )

            config_dir = tmp_path / ".opc" / "config"
            index = _yaml.safe_load((config_dir / "org_index.yaml").read_text())
            org_path = config_dir / "company_orgs" / f"org_{index['active_organization_id']}_config.yaml"
            on_disk = org_path.read_text()
            self.assertIn("New Co", on_disk)
            self.assertIn("ceo", on_disk)
            self.assertFalse((config_dir / "company_index.yaml").exists())

        # In-memory config also reflects the upload.
        self.assertEqual(handler.engine.config.org.company_name, "New Co")
        self.assertEqual(handler.engine.config.org.topology, "Flat")
        self.assertEqual(handler.engine.config.org.final_decider_role_id, "ceo")
        self.assertEqual(len(handler.engine.config.org.roles), 1)
        self.assertEqual(handler.engine.config.org.roles[0].id, "ceo")

        # Handler acknowledged success.
        ws.send_json.assert_awaited()
        last_call = ws.send_json.call_args_list[-1]
        payload = last_call.args[0]
        self.assertTrue(payload.get("payload", {}).get("ok", False))

    async def test_import_drops_existing_talent_templates(self) -> None:
        """Talent templates are outside org config and are dropped on import."""
        from opc.plugins.office_ui.ws_handler import WSHandler
        from opc.core.config import TalentTemplateConfig

        existing = OPCConfig()
        existing.org.talent_templates = [
            TalentTemplateConfig(
                id="pre-existing-template",
                name="Pre-existing",
                category="qa",
                description="should survive the import",
            )
        ]

        handler = WSHandler.__new__(WSHandler)
        handler.engine = SimpleNamespace(
            config=existing,
            org_engine=MagicMock(),
        )
        handler._config_lock = AsyncMock()
        handler._config_lock.__aenter__ = AsyncMock(return_value=None)
        handler._config_lock.__aexit__ = AsyncMock(return_value=None)
        handler._broadcast_org_info = AsyncMock()

        uploaded = {
            "schema_version": 1,
            "company": {"name": "Only updating the company name"},
            "roles": [
                {
                    "id": "lead",
                    "name": "Lead",
                    "responsibility": "Lead.",
                    "reports_to": "owner",
                }
            ],
            "employees": [],
            "escalation_rules": [],
        }
        ws = AsyncMock()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "config").mkdir()
            handler.engine.opc_home = tmp_path / ".opc"
            with patch(
                "opc.core.config._find_project_root",
                return_value=tmp_path,
            ):
                await handler._handle_org_config_import(
                    ws, {"yaml": _yaml.dump(uploaded), "dry_run": False}
                )

        self.assertEqual(handler.engine.config.org.talent_templates, [])
        # And the uploaded name was applied.
        self.assertEqual(
            handler.engine.config.org.company_name, "Only updating the company name"
        )

    async def test_dry_run_does_not_touch_engine_config(self) -> None:
        """dry_run previews the diff but must NOT mutate engine.config."""
        from opc.plugins.office_ui.ws_handler import WSHandler

        existing = OPCConfig()
        existing.org.company_name = "Original"

        handler = WSHandler.__new__(WSHandler)
        handler.engine = SimpleNamespace(
            config=existing,
            org_engine=MagicMock(),
        )
        handler._config_lock = AsyncMock()
        handler._config_lock.__aenter__ = AsyncMock(return_value=None)
        handler._config_lock.__aexit__ = AsyncMock(return_value=None)
        handler._broadcast_org_info = AsyncMock()

        uploaded = {
            "schema_version": 1,
            "company": {"name": "Would-Be-Applied"},
            "roles": [],
        }
        ws = AsyncMock()
        await handler._handle_org_config_import(
            ws, {"yaml": _yaml.dump(uploaded), "dry_run": True}
        )

        # dry_run: memory untouched
        self.assertEqual(handler.engine.config.org.company_name, "Original")


if __name__ == "__main__":
    unittest.main()
