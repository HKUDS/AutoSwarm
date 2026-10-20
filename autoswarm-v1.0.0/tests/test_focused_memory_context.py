from __future__ import annotations

import contextlib
import shutil
import unittest
import uuid
from pathlib import Path

from opc.core.models import Task
from opc.database.store import OPCStore
from opc.layer1_perception.context_assembler import ContextAssembler
from opc.layer5_memory.memory_manager import MemoryManager


@contextlib.contextmanager
def _workspace_tempdir() -> Path:
    base = Path.cwd() / ".tmp-test" / f"focused-memory-{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


class FocusedMemoryContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_focused_memory_context_prefers_relevant_sections(self) -> None:
        with _workspace_tempdir() as root:
            store = OPCStore(root / "tasks.db")
            await store.initialize()
            memory = MemoryManager(root, "proj1", store=store)
            memory.save_memory(
                "# Global\n\n"
                "## Rust Build\n"
                "- Use cargo build for Rust projects.\n\n"
                "## Database Migrations\n"
                "- Always use migrations for schema changes.\n",
                project=False,
            )
            memory.save_memory(
                "# Project\n\n"
                "## Search Tooling\n"
                "- Prefer rg for code search.\n\n"
                "## Browser Checks\n"
                "- Use Playwright for browser verification.\n",
                project=True,
            )
            session_id = "session-focus"
            await memory.ensure_session(session_id, project_id="proj1")
            await memory.update_session_summary(session_id, "Remember the current runtime summary.")

            focused = await memory.build_focused_memory_context(
                query="improve code search with rg",
                project_id="proj1",
                session_id=session_id,
            )
            self.assertIn("Focused Project Memory", focused)
            self.assertIn("Search Tooling", focused)
            self.assertNotIn("Browser Checks", focused)
            self.assertIn("## Session Memory", focused)

            assembler = ContextAssembler(memory)
            core = await assembler.build_core_context(
                Task(
                    title="Search fix",
                    description="Use rg to inspect the repository",
                    project_id="proj1",
                    session_id=session_id,
                )
            )
            self.assertIn("Search Tooling", core)
            self.assertNotIn("Browser Checks", core)
            await store.close()


if __name__ == "__main__":
    unittest.main()
