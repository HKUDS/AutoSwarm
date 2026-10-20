"""Smoke tests for the LLM-judged gate harness.

These tests do not exercise the legacy hardcoded-rule paths (those have
been deleted). They verify that:

1. The harness can be imported and instantiated.
2. With an LLM stub returning a structured `pass` verdict, evaluate()
   returns a pass decision and a populated evidence packet.
3. With a stub returning `rework_same_work_item`, the decision routes back
   to the same work item and carries the rework instructions in `summary`.
4. The stagnation cap upgrades repeated reworks to `escalate`.
5. With no LLM available, evaluate() falls back to `pass` instead of
   blocking the runtime.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from opc.core.models import Task, TaskStatus
from opc.layer2_organization.gate_harness import (
    GateEvidencePacket,
    GateHarness,
    GateHarnessDecision,
)


class _StubLLM:
    """Tiny LLM stub that returns a fixed JSON payload from simple_chat."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, str]] = []

    async def simple_chat(self, *, prompt: str, system: str, task_type: str) -> str:
        self.calls.append({"prompt": prompt, "system": system, "task_type": task_type})
        return json.dumps(self.payload)


def _make_task(*, projection_id: str = "demo_projection", history: list[dict[str, Any]] | None = None) -> Task:
    return Task(
        id="task-1",
        title="Demo work item",
        description="Produce a demo deliverable for the smoke test.",
        assigned_to="demo_role",
        status=TaskStatus.DONE,
        result={"content": "I produced the deliverable as asked."},
        metadata={
            "work_item_projection_id": projection_id,
            "work_item_role_id": "demo_role",
            "work_item_summary": "Demo summary",
            "work_item_summary_for_downstream": "Demo handoff summary",
            "work_item_artifact_index": [
                {"kind": "report", "label": "demo report", "value": "/tmp/demo.md"},
            ],
            "adaptive": {
                "normalized_state": "done",
                "work_item_profile": {
                    "turn_kind": "verify",
                    "blocked_by_signals": ["implementation_ready", "env_ready"],
                },
                "signals": [
                    {"name": "implementation_ready", "required": True, "satisfied": True, "evidence": ["eng-task"]},
                    {"name": "env_ready", "required": True, "satisfied": True, "evidence": ["env-task"]},
                ],
                "hard_dependency_work_item_ids": ["eng-task", "env-task"],
            },
            "success_criteria": ["The deliverable exists", "Summary is non-empty"],
            "gate_harness_history": list(history or []),
        },
    )


class GateHarnessSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_evaluate_pass_path(self) -> None:
        llm = _StubLLM({
            "action": "pass",
            "reason": "Deliverable matches the success criteria.",
        })
        harness = GateHarness(policy={"enabled": True}, llm=llm)
        task = _make_task()
        packet, decision = await harness.evaluate(task, {task.metadata["work_item_projection_id"]: task})

        self.assertIsInstance(packet, GateEvidencePacket)
        self.assertEqual(decision.action, "pass")
        self.assertEqual(decision.source, "llm_judge")
        self.assertEqual(decision.summary, "Deliverable matches the success criteria.")
        self.assertEqual(len(llm.calls), 1)
        # Evidence packet must contain the 5 fields.
        packet_dict = packet.to_dict()
        for key in ("projection_id", "requirements", "output", "prior_rework_count", "prior_rework_feedback"):
            self.assertIn(key, packet_dict)
        self.assertIn("Demo work item", packet_dict["requirements"])
        self.assertIn("I produced the deliverable", packet_dict["output"])

    async def test_evaluate_rework_summary_carries_reason(self) -> None:
        llm = _StubLLM({
            "action": "rework_same_work_item",
            "reason": "Summary missing the required risk section. Add a `## Risks` section listing at least three concrete risks.",
        })
        harness = GateHarness(policy={"enabled": True}, llm=llm)
        task = _make_task()
        _, decision = await harness.evaluate(task, {task.metadata["work_item_projection_id"]: task})

        self.assertEqual(decision.action, "rework_same_work_item")
        self.assertEqual(decision.target_projection_id, "demo_projection")
        self.assertEqual(decision.target_projection_ids, ["demo_projection"])
        self.assertIn("Add a `## Risks` section", decision.summary)

    async def test_decision_outputs_projection_fields_only(self) -> None:
        decision = GateHarnessDecision(
            action="rework_same_work_item",
            summary="Fix it.",
            target_projection_id="projection-target",
            target_projection_ids=["projection-target", "second-target"],
        )

        self.assertEqual(decision.target_projection_id, "projection-target")
        self.assertEqual(decision.target_projection_ids, ["projection-target", "second-target"])
        payload = decision.to_dict()
        self.assertEqual(payload["target_projection_id"], "projection-target")
        self.assertEqual(payload["target_projection_ids"], ["projection-target", "second-target"])
        self.assertEqual(
            set(payload),
            {
                "action",
                "summary",
                "target_projection_id",
                "target_projection_ids",
                "notes",
                "constraints",
                "blockers",
                "blocker_types",
                "residual_risks",
                "source",
                "blocker_fingerprint",
            },
        )

    async def test_stagnation_escalates_on_same_fingerprint_streak(self) -> None:
        """Stagnation triggers only when consecutive trailing reworks share
        the same fingerprint (same problem repeating). Different-fingerprint
        reworks (different problems being fixed) do NOT count."""
        reason = "Same blocker again. Try harder."
        llm = _StubLLM({
            "action": "rework_same_work_item",
            "reason": reason,
        })
        harness = GateHarness(policy={"enabled": True, "stagnation_threshold": 3}, llm=llm)
        # Compute the fingerprint the judge will produce for this reason.
        fp = GateHarness._fingerprint("rework_same_work_item", reason)
        # Two prior reworks with SAME fingerprint → this one makes 3 → escalate.
        history = [
            {"action": "rework_same_work_item", "summary": "x", "blocker_fingerprint": fp},
            {"action": "rework_same_work_item", "summary": "x", "blocker_fingerprint": fp},
        ]
        task = _make_task(history=history)
        _, decision = await harness.evaluate(task, {task.metadata["work_item_projection_id"]: task})

        self.assertEqual(decision.action, "escalate")
        self.assertIn("stagnation", decision.summary)
        self.assertIn("stagnation_cap", decision.source)

    async def test_different_fingerprints_do_not_trigger_stagnation(self) -> None:
        """Many reworks for different problems should NOT trigger stagnation."""
        llm = _StubLLM({
            "action": "rework_same_work_item",
            "reason": "New problem this time.",
        })
        harness = GateHarness(policy={"enabled": True, "stagnation_threshold": 3}, llm=llm)
        # 5 prior reworks but ALL with different fingerprints.
        history = [
            {"action": "rework_same_work_item", "summary": "a", "blocker_fingerprint": "fp1"},
            {"action": "rework_same_work_item", "summary": "b", "blocker_fingerprint": "fp2"},
            {"action": "rework_same_work_item", "summary": "c", "blocker_fingerprint": "fp3"},
            {"action": "rework_same_work_item", "summary": "d", "blocker_fingerprint": "fp4"},
            {"action": "rework_same_work_item", "summary": "e", "blocker_fingerprint": "fp5"},
        ]
        task = _make_task(history=history)
        _, decision = await harness.evaluate(task, {task.metadata["work_item_projection_id"]: task})

        # Should NOT escalate — each rework was for a different problem.
        self.assertEqual(decision.action, "rework_same_work_item")

    async def test_no_llm_falls_back_to_pass(self) -> None:
        harness = GateHarness(policy={"enabled": True}, llm=None)
        task = _make_task()
        _, decision = await harness.evaluate(task, {task.metadata["work_item_projection_id"]: task})

        self.assertEqual(decision.action, "pass")
        self.assertEqual(decision.source, "llm_judge_fallback")
        self.assertIn("Judge unavailable", decision.summary)

    async def test_judge_runner_takes_precedence_over_llm(self) -> None:
        # When a judge_runner callback is supplied, the harness must use
        # it instead of the simple_chat llm fallback. This is the hook
        # company_mode uses to dispatch to the same external agent type
        # in a fresh session.
        seen_packets: list[dict[str, Any]] = []
        seen_source_task_ids: list[str] = []

        async def fake_runner(packet: GateEvidencePacket, system_prompt: str, source_task: Task) -> str:
            seen_packets.append(packet.to_dict())
            seen_source_task_ids.append(source_task.id)
            return json.dumps({
                "action": "pass",
                "reason": "judge_runner pathway exercised",
            })

        # llm stub would say "rework"; runner says "pass". Runner must win.
        rebellious_llm = _StubLLM({
            "action": "rework_same_work_item",
            "reason": "should not be used",
        })
        harness = GateHarness(policy={"enabled": True}, llm=rebellious_llm, judge_runner=fake_runner)
        task = _make_task()
        _, decision = await harness.evaluate(task, {task.metadata["work_item_projection_id"]: task})

        self.assertEqual(decision.action, "pass")
        self.assertEqual(decision.summary, "judge_runner pathway exercised")
        self.assertEqual(len(seen_packets), 1)
        self.assertEqual(seen_source_task_ids, [task.id])
        self.assertEqual(len(rebellious_llm.calls), 0)  # llm fallback not used

    async def test_unknown_action_falls_back_to_pass(self) -> None:
        llm = _StubLLM({
            "action": "definitely_not_a_real_action",
            "reason": "garbage",
        })
        harness = GateHarness(policy={"enabled": True}, llm=llm)
        task = _make_task()
        _, decision = await harness.evaluate(task, {task.metadata["work_item_projection_id"]: task})

        self.assertEqual(decision.action, "pass")
        self.assertEqual(decision.source, "llm_judge_fallback")


if __name__ == "__main__":
    unittest.main()
