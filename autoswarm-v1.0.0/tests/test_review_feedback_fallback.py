"""Regression test for ``_review_feedback_with_fallback``.

The new14 silent-rework-loop bug had two contributing layers:
the prompt assembler dropped the feedback (fixed in
``test_turn_mode_context.py``), and on the producer side
``_finalize_review_work_item`` would write an empty
``rework_feedback`` to the worker's metadata if the reviewer
agent didn't emit a structured ``review_verdict`` JSON. Even
after fixing the assembler, an empty feedback string would
still leave the worker with nothing actionable.

This test pins the producer-side fix: when the reviewer's
structured verdict is missing or empty, salvage the raw
content from ``review_task.result`` so the worker still sees
*something*. Mirrors the gate-path salvage at
``_apply_review_gate``.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from opc.core.models import Task, TaskStatus
from opc.layer2_organization.company_mode import CompanyWorkItemExecutor


def _executor() -> CompanyWorkItemExecutor:
    async def _noop(*_a, **_kw):
        return None

    return CompanyWorkItemExecutor(
        org_engine=MagicMock(),
        communication=None,
        approval_engine=None,
        memory=None,
        execute_task=_noop,
        save_task=_noop,
    )


def _review_task(*, metadata=None, result=None) -> Task:
    return Task(
        id="rt",
        title="Review",
        description="",
        assigned_to="ceo",
        status=TaskStatus.DONE,
        project_id="p",
        session_id="s",
        metadata=dict(metadata or {}),
        result=result,
    )


class ReviewFeedbackWithFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ex = _executor()

    def test_uses_structured_summary_when_present(self) -> None:
        task = _review_task(
            metadata={
                "structured_review_verdict": {
                    "label": "reject",
                    "summary": "Missing handoff file.",
                    "blocking_issues": ["create cto_app_architecture.md"],
                    "followups": [],
                },
            },
            result={"content": "raw content - should NOT be used"},
        )
        feedback = self.ex._review_feedback_with_fallback(task)
        self.assertIn("Missing handoff file.", feedback)
        self.assertIn("create cto_app_architecture.md", feedback)
        self.assertNotIn("should NOT be used", feedback)

    def test_falls_back_to_result_content_when_verdict_missing(self) -> None:
        # The pathological path that triggered new14's loop: reviewer
        # said useful things in prose but the structured verdict
        # parser produced nothing.
        task = _review_task(
            metadata={},  # no structured_review_verdict at all
            result={
                "content": (
                    "Verdict: Rework. The handoff file under "
                    "trans_app/handoffs/cto_app_architecture.md is missing."
                ),
            },
        )
        feedback = self.ex._review_feedback_with_fallback(task)
        self.assertIn("trans_app/handoffs/cto_app_architecture.md", feedback)
        self.assertIn("Rework", feedback)

    def test_falls_back_when_verdict_label_set_but_summary_blank(self) -> None:
        # Defensive: the verdict can be present but stripped (e.g.
        # the agent emitted ``{"label": "reject"}`` with no summary
        # or issues). Without a fallback we'd still write empty
        # feedback to the worker.
        task = _review_task(
            metadata={"structured_review_verdict": {"label": "reject"}},
            result={"content": "Please redo step 4 — output is empty."},
        )
        feedback = self.ex._review_feedback_with_fallback(task)
        self.assertIn("Please redo step 4", feedback)

    def test_returns_empty_when_no_verdict_and_no_result(self) -> None:
        # Both sources empty → no feedback to manufacture.
        task = _review_task(metadata={}, result=None)
        self.assertEqual(self.ex._review_feedback_with_fallback(task), "")

    def test_supports_taskresult_object_as_well_as_dict(self) -> None:
        task = _review_task(
            metadata={},
            result=SimpleNamespace(content="object-style content"),
        )
        feedback = self.ex._review_feedback_with_fallback(task)
        self.assertIn("object-style content", feedback)


if __name__ == "__main__":
    unittest.main()
