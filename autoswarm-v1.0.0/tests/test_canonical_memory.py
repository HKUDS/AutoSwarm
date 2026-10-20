from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opc.layer5_memory.markdown_memory import MarkdownMemoryStore
from opc.layer5_memory.memory_manager import MemoryManager


class CanonicalMemoryTests(unittest.TestCase):
    def test_save_and_append_use_canonical_markdown_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            memory = MemoryManager(root, "proj1")

            memory.save_memory("# Global\n- global preference", project=False)
            memory.save_memory("# Project\n- project preference", project=True)
            memory.append_memory("- appended project fact", project=True)

            global_path = root / "memory" / "global.md"
            project_path = root / "memory" / "projects" / "proj1.md"
            legacy_project_path = root / "projects" / "proj1" / "MEMORY.md"

            self.assertTrue(global_path.exists())
            self.assertTrue(project_path.exists())
            self.assertFalse(legacy_project_path.exists())
            self.assertIn("global preference", global_path.read_text(encoding="utf-8"))
            project_text = project_path.read_text(encoding="utf-8")
            self.assertIn("project preference", project_text)
            self.assertIn("appended project fact", project_text)
            self.assertNotIn("OPC_STRUCTURED_MEMORY", project_text)

    def test_legacy_visible_markdown_migrates_and_hidden_files_are_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            global_legacy = root / "memory" / "MEMORY.md"
            project_root = root / "projects" / "proj1"
            project_legacy = project_root / "MEMORY.md"
            global_legacy.parent.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            hidden = (
                "<!-- OPC_STRUCTURED_MEMORY:BEGIN -->\n"
                "```yaml\npreferred_language: Chinese\n```\n"
                "<!-- OPC_STRUCTURED_MEMORY:END -->\n"
            )
            global_legacy.write_text("# Old Global\n- visible global\n\n" + hidden, encoding="utf-8")
            project_legacy.write_text("# Old Project\n- visible project\n\n" + hidden, encoding="utf-8")
            (root / "memory" / "HISTORY.md").write_text("old history", encoding="utf-8")
            (project_root / "HISTORY.md").write_text("old project history", encoding="utf-8")
            (project_root / "project_profile.yaml").write_text("legacy: true\n", encoding="utf-8")
            (project_root / "secretary_profile.yaml").write_text("legacy: true\n", encoding="utf-8")
            profiles_dir = root / "profiles"
            profiles_dir.mkdir()
            (profiles_dir / "profile.yaml").write_text("legacy: true\n", encoding="utf-8")

            store = MarkdownMemoryStore(root)

            global_text = store.load_visible_text()
            project_text = store.load_visible_text("proj1")
            self.assertIn("visible global", global_text)
            self.assertIn("visible project", project_text)
            self.assertNotIn("OPC_STRUCTURED_MEMORY", global_text)
            self.assertNotIn("preferred_language", project_text)
            self.assertFalse(global_legacy.exists())
            self.assertFalse(project_legacy.exists())
            self.assertFalse((root / "memory" / "HISTORY.md").exists())
            self.assertFalse((project_root / "HISTORY.md").exists())
            self.assertFalse((project_root / "project_profile.yaml").exists())
            self.assertFalse((project_root / "secretary_profile.yaml").exists())
            self.assertFalse(profiles_dir.exists())

    def test_project_memory_is_namespaced_by_project_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            one = MemoryManager(root, "proj1")
            two = MemoryManager(root, "proj2")

            one.save_memory("- proj1 only", project=True)
            two.save_memory("- proj2 only", project=True)

            self.assertIn("proj1 only", one.load_project_memory_markdown("proj1"))
            self.assertNotIn("proj2 only", one.load_project_memory_markdown("proj1"))
            self.assertIn("proj2 only", two.load_project_memory_markdown("proj2"))


if __name__ == "__main__":
    unittest.main()
