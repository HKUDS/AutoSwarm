"""Work-item metadata sync: verify context_assembler reads employee prompt /
delta / progress_log via ``WorkItemContextView`` with proper fallback.

Covers:
* ``_build_self_section`` reads ``employee_prompt_context`` /
  ``employee_delta_context`` from work_item side when mirrored (company mode)
* Falls back to task.metadata when view is None / task-mode
* ``_build_working_summary`` reads ``progress_log`` with same precedence
* Precedence (wi wins) when both sides populated
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from opc.core.models import Task
from opc.layer1_perception.context_assembler import ContextAssembler
from opc.layer2_organization.work_item_context_view import WorkItemContextView


def _assembler() -> ContextAssembler:
    """Build an assembler sufficient for the context helpers under test.

    The helpers we target (_build_self_section, _build_working_summary)
    don't touch memory / store directly, so mocks are enough.
    """
    return ContextAssembler(memory=MagicMock(), store=None)


def _wi(metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(metadata=metadata or {})


def _task(metadata: dict | None = None, context_snapshot: dict | None = None) -> Task:
    return Task(
        id="t-1",
        title="t",
        metadata=metadata or {},
        context_snapshot=context_snapshot or {},
    )


class BuildSelfSectionViewTests(unittest.TestCase):
    def test_prompt_context_from_work_item_when_mirrored(self) -> None:
        assembler = _assembler()
        task = _task({"employee_assignment": {"name": "Alice"}})
        view = WorkItemContextView(
            _wi({"employee_prompt_context": "WI persona content"}),
            task,
        )
        out = assembler._build_self_section(task, view)
        self.assertIn("### Employee Persona", out)
        self.assertIn("WI persona content", out)

    def test_prompt_context_falls_back_to_task_metadata(self) -> None:
        assembler = _assembler()
        task = _task({"employee_prompt_context": "Task persona content"})
        view = WorkItemContextView(_wi({}), task)
        out = assembler._build_self_section(task, view)
        self.assertIn("Task persona content", out)

    def test_no_view_passed_degrades_to_task_only(self) -> None:
        """Backwards-compat: callers that don't pass view still get the
        task-side fallback (view=None constructs task-only internally)."""
        assembler = _assembler()
        task = _task({"employee_prompt_context": "fallback"})
        out = assembler._build_self_section(task)
        self.assertIn("fallback", out)

    def test_wi_wins_over_task_when_both_populated(self) -> None:
        assembler = _assembler()
        task = _task({"employee_prompt_context": "task-loses"})
        view = WorkItemContextView(
            _wi({"employee_prompt_context": "wi-wins"}),
            task,
        )
        out = assembler._build_self_section(task, view)
        self.assertIn("wi-wins", out)
        self.assertNotIn("task-loses", out)

    def test_delta_context_from_work_item(self) -> None:
        assembler = _assembler()
        task = _task({})
        view = WorkItemContextView(
            _wi({"employee_delta_context": "wi-delta"}),
            task,
        )
        out = assembler._build_self_section(task, view)
        self.assertIn("### Learned Working Profile", out)
        self.assertIn("wi-delta", out)

    def test_empty_everything_returns_empty(self) -> None:
        assembler = _assembler()
        task = _task({})
        view = WorkItemContextView(_wi({}), task)
        self.assertEqual(assembler._build_self_section(task, view), "")


class BuildWorkingSummaryViewTests(unittest.TestCase):
    def test_progress_log_from_work_item(self) -> None:
        assembler = _assembler()
        task = _task({})
        view = WorkItemContextView(
            _wi({"progress_log": ["wi step 1", "wi step 2"]}),
            task,
        )
        out = assembler._build_working_summary(task, view)
        self.assertIn("Recent progress:", out)
        self.assertIn("wi step 1", out)
        self.assertIn("wi step 2", out)

    def test_progress_log_fallback_from_task_metadata(self) -> None:
        assembler = _assembler()
        task = _task({"progress_log": ["task fallback step"]})
        view = WorkItemContextView(_wi({}), task)
        out = assembler._build_working_summary(task, view)
        self.assertIn("task fallback step", out)

    def test_no_view_passed_still_reads_task_metadata(self) -> None:
        assembler = _assembler()
        task = _task({"progress_log": ["legacy step"]})
        out = assembler._build_working_summary(task)
        self.assertIn("legacy step", out)

    def test_progress_log_limited_to_last_4(self) -> None:
        """Prompt budget guard: summary renders only last 4 items."""
        assembler = _assembler()
        task = _task({})
        view = WorkItemContextView(
            _wi({"progress_log": ["s1", "s2", "s3", "s4", "s5", "s6"]}),
            task,
        )
        out = assembler._build_working_summary(task, view)
        self.assertIn("s3", out)
        self.assertIn("s4", out)
        self.assertIn("s5", out)
        self.assertIn("s6", out)
        self.assertNotIn("s1", out)
        self.assertNotIn("s2", out)


if __name__ == "__main__":
    unittest.main()
