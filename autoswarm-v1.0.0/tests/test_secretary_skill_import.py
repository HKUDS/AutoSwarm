from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from opc.layer2_organization.secretary import SecretaryService
from opc.layer5_memory.preference import PreferenceManager
from opc.layer5_memory.secretary_policy import SecretaryPolicyManager
from opc.layer5_memory.skill_importer import ExternalSkillImporter, SkillImportResult
from opc.layer5_memory.skill_library import SkillLibrary


class _StubMemory:
    async def ensure_session(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_user_turn(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_assistant_turn(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def build_project_knowledge_context(self, project_id: str | None = None) -> str:
        return ""

    async def build_session_prompt_context(
        self,
        session_id: str,
        *,
        include_latest_user_turn: bool = True,
    ) -> str:
        _ = (session_id, include_latest_user_turn)
        return ""


class _StubStore:
    async def get_events(self, limit: int = 12) -> list[Any]:
        return []


class _StubLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def simple_chat(self, prompt: str, system: str | None = None, task_type: str | None = None) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


class _StubImporter:
    async def import_skill(
        self,
        *,
        project_id: str,
        source: str = "clawhub",
        query: str = "",
        slug: str = "",
        path: str = "",
        domains: list[str] | None = None,
        enable: bool = True,
    ) -> SkillImportResult:
        return SkillImportResult(
            skill_name="remote-build-helper",
            skill_path=f"/tmp/{project_id}/skills/remote-build-helper",
            source_slug=slug or "remote-build-helper",
            validation_message="Skill is valid!",
            available=True,
            enabled_domains=list(domains or []) if enable else [],
        )


class ExternalSkillImporterTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_skill_normalizes_and_enables_project_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            opc_home = Path(tmpdir)
            skills = SkillLibrary(opc_home)
            policies = SecretaryPolicyManager(opc_home)

            async def runner(argv: list[str], cwd: Path) -> tuple[int, str, str]:
                command = argv[3]
                if command == "search":
                    return 0, "1. remote-build-helper - Build helper for CI pipelines\n", ""
                if command == "install":
                    project_root = Path(argv[-1])
                    skill_dir = project_root / "skills" / "remote-build-helper"
                    skill_dir.mkdir(parents=True, exist_ok=True)
                    (skill_dir / "SKILL.md").write_text(
                        "---\n"
                        "name: Remote Build Helper\n"
                        "homepage: https://example.com/skill\n"
                        "unexpected-key: keep-me\n"
                        "---\n\n"
                        "# Remote Build Helper\n\n"
                        "Use this skill for remote build automation.\n",
                        encoding="utf-8",
                    )
                    (skill_dir / "README.txt").write_text("extra root file", encoding="utf-8")
                    (skill_dir / "docs").mkdir(parents=True, exist_ok=True)
                    (skill_dir / "docs" / "notes.md").write_text("nested docs", encoding="utf-8")
                    return 0, "installed", ""
                raise AssertionError(f"Unexpected command: {argv}")

            importer = ExternalSkillImporter(skill_library=skills, policies=policies, command_runner=runner)
            result = await importer.import_skill(
                project_id="proj1",
                query="remote build helper",
                domains=["coding"],
                enable=True,
            )

            skill_dir = opc_home / "projects" / "proj1" / "skills" / result.skill_name
            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertTrue((skill_dir / "assets" / "imported-root" / "README.txt").exists())
            self.assertTrue((skill_dir / "assets" / "imported-root" / "docs" / "notes.md").exists())
            self.assertIsNotNone(skills.get("remote-build-helper"))
            self.assertEqual(policies.get_injected_skills("proj1", ["coding"]), [])

            frontmatter_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8").split("---\n", 2)[1]
            frontmatter = yaml.safe_load(frontmatter_text)
            self.assertEqual(frontmatter["name"], "remote-build-helper")
            self.assertIn("imported_from", frontmatter["metadata"])
            self.assertIn("imported_frontmatter", frontmatter["metadata"])

    async def test_import_skill_from_local_path_normalizes_and_enables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            opc_home = Path(tmpdir)
            skills = SkillLibrary(opc_home)
            policies = SecretaryPolicyManager(opc_home)

            source_dir = opc_home / "downloaded-skill"
            source_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / "SKILL.md").write_text(
                "---\n"
                "name: Downloaded Skill\n"
                "description: Local downloaded skill.\n"
                "---\n\n"
                "# Downloaded Skill\n\n"
                "Use this for downloaded workflows.\n",
                encoding="utf-8",
            )
            (source_dir / "misc.txt").write_text("extra root file", encoding="utf-8")

            importer = ExternalSkillImporter(skill_library=skills, policies=policies)
            result = await importer.import_skill(
                project_id="proj1",
                source="path",
                path=str(source_dir),
                domains=["coding"],
                enable=True,
            )

            skill_dir = opc_home / "projects" / "proj1" / "skills" / result.skill_name
            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertTrue((skill_dir / "assets" / "imported-root" / "misc.txt").exists())
            self.assertEqual(policies.get_injected_skills("proj1", ["coding"]), [])
            self.assertIsNotNone(skills.get("downloaded-skill"))


class SecretarySkillActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_message_applies_import_skill_action(self) -> None:
        root = Path(tempfile.mkdtemp())
        llm = _StubLLM(
            {
                "response": "已按你的要求导入并启用该 skill。",
                "memory_notes": [],
                "actions": [
                    {
                        "kind": "import_skill",
                        "scope": "project",
                        "source": "clawhub",
                        "query": "remote build helper",
                        "domains": ["coding"],
                        "enable": True,
                    }
                ],
                "rule_updates": [],
            }
        )
        service = SecretaryService(
            llm=llm,
            store=_StubStore(),
            memory=_StubMemory(),
            preferences=PreferenceManager(root),
            skills=SkillLibrary(root),
            policies=SecretaryPolicyManager(root),
        )
        service.skill_importer = _StubImporter()

        payload = await service.handle_message(
            "请帮我导入一个 remote build helper skill，并让 coding 任务直接可用。",
            project_id="proj1",
            session_id="sess-1",
        )

        self.assertIn("Applied secretary actions", payload["response"])
        self.assertEqual(
            payload["applied_actions"],
            ["imported skill `remote-build-helper` and made it available in project `proj1`; auto-injected for coding"],
        )

    async def test_handle_message_skips_project_preference_updates(self) -> None:
        root = Path(tempfile.mkdtemp())
        preferences = PreferenceManager(root)
        llm = _StubLLM(
            {
                "response": "以后会按你的偏好来回复。",
                "memory_notes": [],
                "actions": [
                    {
                        "kind": "update_preferences",
                        "scope": "project",
                        "preferred_language": "Chinese",
                        "communication_style": "brief, direct, and action-oriented",
                    }
                ],
                "rule_updates": [],
            }
        )
        service = SecretaryService(
            llm=llm,
            store=_StubStore(),
            memory=_StubMemory(),
            preferences=preferences,
            skills=SkillLibrary(root),
            policies=SecretaryPolicyManager(root),
        )

        payload = await service.handle_message(
            "以后这个项目里你用中文，而且说话尽量短一点，直接说结论。",
            project_id="proj1",
            session_id="sess-2",
        )

        self.assertEqual(preferences.load_project("proj1"), {})
        self.assertEqual(
            payload["applied_actions"],
            ["skipped preference update because secretary memory writes are disabled"],
        )


if __name__ == "__main__":
    unittest.main()
