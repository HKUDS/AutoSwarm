"""LLM-judged gate harness for company-mode work items.

This module used to encode ~700 lines of hard-coded "if blocker_type ==
X then action = Y" rules. That approach kept misclassifying real
deliveries (e.g. reading `ready_for_final_release: false` as a hard
release blocker even when the same report explicitly said
`ready_for_downstream_execution: true`) and could not generalize across
the variety of user tasks the runtime is supposed to handle.

The current design is intentionally minimal:

1. Build a *descriptive* evidence packet for the completed work item. The
   packet only describes what happened — it makes no policy decisions.
   It contains: the work item's original requirements, the upstream context
   the agent saw, the agent's actual output (result + summary + artifact
   index + work_item_summary_for_downstream), the agent's own self-reported risks and
   blockers, and a small number of cheap objective signals (task status,
   dependency health, prior rework history).

2. Hand the packet to an LLM judge with a tightly scoped prompt. The
   judge compares "what was asked" vs "what was produced" and returns
   one of exactly three actions: `pass`, `rework_same_work_item`, or
   `escalate`. The reason text is fed back to the original agent
   session via `gate_harness_rework_feedback` so the same agent can fix
   it on the next turn.

3. Apply a single safety net: a stagnation cap. If the same blocker
   fingerprint has caused N reworks in a row without converging,
   upgrade to `escalate` so the user gets a chance to break the loop.
   This is the only deterministic decision the harness still makes.

If the LLM is unavailable, evaluate() returns `pass` with a logged
warning rather than blocking — the runtime is designed to trust agent
self-reports when there is no second opinion available.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from opc.core.config import GateHarnessPolicyConfig
from opc.core.models import Task
from opc.layer2_organization.work_item_identity import projection_id_for_task
from opc.llm.retry import LLMRetryError, call_llm_json_with_retry

logger = logging.getLogger(__name__)


# Callback signature for spawning a fresh judge session.
#
# The runner is expected to:
#   1. Inspect `source_task.assigned_external_agent` (or fall back to a
#      native model) to pick the SAME agent type that produced the
#      work item being judged. The point is consistency — codex-produced
#      work is judged by codex, claude_code-produced work by
#      claude_code, native-produced work by the native LLM.
#   2. Open a NEW session for the judge (not a resume of the source
#      agent's session) so the judge starts with a clean context and
#      cannot be biased by the agent's prior reasoning trace.
#   3. Send `system_prompt` as the system message and the JSON-encoded
#      packet as the user message.
#   4. Return the raw text the judge produced.
#
# gate_harness then parses that text as JSON. If the runner returns
# anything that doesn't parse, the harness falls back to `pass` with a
# logged warning rather than blocking the runtime.
JudgeRunner = Callable[
    ["GateEvidencePacket", str, Task],
    Awaitable[str],
]


GATE_HARNESS_AGENT_PROMPT = """\
You are the gate-harness judge for one completed work item of an AI company runtime.

You receive an evidence packet describing what the work item was asked
to produce, what context the agent had, and what the agent actually
produced. Decide whether the output meets the work item's requirements.

Return STRICT JSON with exactly these two fields and no extras:
{
  "action": "pass" | "rework_same_work_item" | "escalate",
  "reason": "<your explanation; if action is rework_same_work_item, include the concrete actionable steps the agent should take>"
}

Action semantics:
- `pass` — the deliverables meet the stated requirements.
- `rework_same_work_item` — the same agent can fix the gap; your `reason`
  must spell out what is wrong and what to do about it.
- `escalate` — the gap needs a human decision, OR the same problem
  has already caused multiple unsuccessful reworks.

Return JSON only — no markdown fences, no commentary.
"""


@dataclass
class GateEvidencePacket:
    """Minimal snapshot fed to the LLM judge: 5 fields only.

    - requirements: what the work item was asked to produce (task.title +
      task.description — the "task brief" that was given to the agent)
    - output: what the agent actually produced (task.result raw stdout)
    - prior_rework_count / prior_rework_feedback: history of previous
      gate judge rework cycles for this work item
    """

    projection_id: str
    requirements: str
    output: str
    prior_rework_count: int = 0
    prior_rework_feedback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "requirements": self.requirements,
            "output": self.output,
            "prior_rework_count": self.prior_rework_count,
            "prior_rework_feedback": self.prior_rework_feedback,
        }


@dataclass
class GateHarnessDecision:
    """Projection-only decision returned to company_mode."""

    action: str
    summary: str
    target_projection_id: str = ""
    target_projection_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    blocker_types: list[str] = field(default_factory=list)
    residual_risks: list[str] = field(default_factory=list)
    source: str = "llm_judge"
    blocker_fingerprint: str = ""

    def __post_init__(self) -> None:
        projection_id = str(self.target_projection_id or "").strip()
        projection_ids = [
            str(item).strip()
            for item in list(self.target_projection_ids or [])
            if str(item).strip()
        ]
        if not projection_ids and projection_id:
            projection_ids = [projection_id]
        self.target_projection_id = projection_id or (projection_ids[0] if projection_ids else "")
        self.target_projection_ids = projection_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "summary": self.summary,
            "target_projection_id": self.target_projection_id,
            "target_projection_ids": list(self.target_projection_ids),
            "notes": list(self.notes),
            "constraints": list(self.constraints),
            "blockers": list(self.blockers),
            "blocker_types": list(self.blocker_types),
            "residual_risks": list(self.residual_risks),
            "source": self.source,
            "blocker_fingerprint": self.blocker_fingerprint,
        }


class GateHarness:
    """Evaluates one completed work item using an LLM judge."""

    ALLOWED_ACTIONS = ("pass", "rework_same_work_item", "escalate")

    def __init__(
        self,
        *,
        policy: GateHarnessPolicyConfig | dict[str, Any] | None = None,
        llm: Any | None = None,
        org_engine: Any | None = None,
        judge_runner: JudgeRunner | None = None,
    ) -> None:
        if isinstance(policy, GateHarnessPolicyConfig):
            self.policy = policy
        else:
            self.policy = GateHarnessPolicyConfig.model_validate(dict(policy or {}))
        self.llm = llm
        self.org_engine = org_engine
        # Preferred path: company_mode wires a runner that spawns a
        # fresh session of the SAME external agent type that produced
        # the work item being judged (codex → codex, claude_code →
        # claude_code, native → native llm). When `judge_runner` is
        # set, `_invoke_judge` uses it. Otherwise we fall back to
        # `self.llm.simple_chat` so the harness still works in tests
        # and in setups that have not yet wired the per-agent dispatch.
        self.judge_runner = judge_runner

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        task: Task,
        task_by_projection_id: dict[str, Task],
    ) -> tuple[GateEvidencePacket, GateHarnessDecision]:
        packet = self.build_evidence_packet(task, task_by_projection_id)
        decision = await self._invoke_judge(packet, source_task=task)
        decision = self._apply_stagnation_cap(task, decision)
        return packet, decision

    # ------------------------------------------------------------------
    # Evidence packet construction (Class B: descriptive only)
    # ------------------------------------------------------------------

    def build_evidence_packet(
        self,
        task: Task,
        task_by_projection_id: dict[str, Task],
    ) -> GateEvidencePacket:
        metadata = dict(task.metadata or {})
        projection_id = projection_id_for_task(task)

        # Requirements = the task brief the agent received.
        requirements = f"{task.title or ''}\n\n{task.description or ''}".strip()

        # Output = agent's raw stdout result.
        if isinstance(task.result, dict):
            output = str(task.result.get("content", "") or "")
        elif task.result is not None:
            output = str(task.result)
        else:
            output = ""

        return GateEvidencePacket(
            projection_id=projection_id,
            requirements=requirements,
            output=output,
            prior_rework_count=int(metadata.get("gate_harness_rework_count", 0) or 0),
            prior_rework_feedback=str(metadata.get("gate_harness_rework_feedback", "") or "").strip(),
        )

    # ------------------------------------------------------------------
    # LLM judge
    # ------------------------------------------------------------------

    async def _invoke_judge(
        self,
        packet: GateEvidencePacket,
        *,
        source_task: Task,
    ) -> GateHarnessDecision:
        # Preferred: company_mode-supplied runner that spawns a fresh
        # session of the SAME external agent type that produced this
        # work item (codex → codex, etc.). Fallback: native simple_chat.
        # Strategy: the judge_runner path spawns a fresh agent session to
        # produce the JSON. Per system design, errors inside a running
        # agent are handled by the agent itself, so we keep that path
        # single-shot. Only the simple_chat fallback is wrapped in the
        # retry helper, because there is no agent to self-correct.
        if self.judge_runner is not None:
            try:
                raw = await self.judge_runner(packet, GATE_HARNESS_AGENT_PROMPT, source_task)
            except Exception as exc:  # pragma: no cover - runner transport errors
                logger.warning(
                    "[gate_harness] Judge runner failed for work item `%s`: %s; defaulting to pass.",
                    packet.projection_id,
                    exc,
                )
                return self._fallback_pass(packet, reason=f"Judge runner failed: {exc}")

            try:
                data = json.loads(self._strip_markdown_fences(raw))
            except Exception:
                logger.warning(
                    "[gate_harness] Judge runner returned non-JSON for work item `%s`; defaulting to pass. Raw=%r",
                    packet.projection_id,
                    str(raw)[:200],
                )
                return self._fallback_pass(packet, reason="Judge runner returned non-JSON output.")
            if not isinstance(data, dict):
                return self._fallback_pass(packet, reason="Judge runner returned non-object output.")
            action = str(data.get("action", "") or "").strip()
            if action not in self.ALLOWED_ACTIONS:
                logger.warning(
                    "[gate_harness] Judge runner returned unknown action `%s` for work item `%s`; defaulting to pass.",
                    action,
                    packet.projection_id,
                )
                return self._fallback_pass(packet, reason=f"Judge runner returned unrecognized action `{action}`.")
        elif self.llm is not None:
            allowed_actions = self.ALLOWED_ACTIONS

            def _validate_judge_response(parsed: Any) -> str | None:
                if not isinstance(parsed, dict):
                    return "Top-level response must be a JSON object."
                act = str(parsed.get("action", "") or "").strip()
                if act not in allowed_actions:
                    return (
                        f"Unknown action `{act}`. Choose one of: "
                        f"{', '.join(sorted(allowed_actions))}."
                    )
                return None

            try:
                data = await call_llm_json_with_retry(
                    self.llm,
                    system=GATE_HARNESS_AGENT_PROMPT,
                    payload=packet.to_dict(),
                    task_type="quick_tasks",
                    validator=_validate_judge_response,
                    label=f"gate_harness:{packet.projection_id}",
                )
            except LLMRetryError as exc:
                logger.warning(
                    "[gate_harness] simple_chat judge fallback failed for work item `%s` after retries: %s; defaulting to pass.",
                    packet.projection_id,
                    exc.last_error,
                )
                return self._fallback_pass(
                    packet,
                    reason=f"LLM judge failed after retries: {exc.last_error}",
                )
            action = str(data.get("action", "") or "").strip()
        else:
            logger.warning(
                "[gate_harness] No judge_runner and no llm available for work item `%s`; defaulting to pass.",
                packet.projection_id,
            )
            return self._fallback_pass(packet, reason="Judge unavailable; trusting agent self-report.")

        reason = str(data.get("reason", "") or "").strip() or "(no reason provided)"

        # `reason` is the canonical text fed back to the agent's session
        # via `gate_harness_rework_feedback` on the next turn. The judge
        # is instructed to put any "what to fix and how" content directly
        # in `reason` when the action is rework_same_work_item.
        return GateHarnessDecision(
            action=action,
            summary=reason,
            target_projection_id=packet.projection_id if action == "rework_same_work_item" else "",
            target_projection_ids=[packet.projection_id] if action == "rework_same_work_item" else [],
            source="llm_judge",
            blocker_fingerprint=self._fingerprint(action, reason),
        )

    def _fallback_pass(self, packet: GateEvidencePacket, *, reason: str) -> GateHarnessDecision:
        return GateHarnessDecision(
            action="pass",
            summary=reason,
            source="llm_judge_fallback",
        )

    # ------------------------------------------------------------------
    # Stagnation cap (Class C: the only deterministic decision left)
    # ------------------------------------------------------------------

    def _apply_stagnation_cap(
        self,
        task: Task,
        decision: GateHarnessDecision,
    ) -> GateHarnessDecision:
        """Escalate only when the SAME problem keeps repeating.

        Total rework count is irrelevant — a work item that gets reworked
        10 times for 10 different reasons is healthy iteration (e.g.
        user keeps asking for changes). What indicates stagnation is
        N consecutive reworks whose fingerprints match: the judge
        keeps saying the same thing, meaning the agent is unable to
        fix the problem.

        We count consecutive trailing rework entries in history that
        share the current decision's fingerprint. If that streak
        reaches the threshold, we escalate.
        """
        if decision.action != "rework_same_work_item":
            return decision
        threshold = max(2, int(getattr(self.policy, "stagnation_threshold", 3) or 3))
        current_fp = decision.blocker_fingerprint
        if not current_fp:
            return decision

        history = [
            dict(item)
            for item in list(task.metadata.get("gate_harness_history", []) or [])
            if isinstance(item, dict)
        ]

        # Count consecutive trailing reworks with the same fingerprint.
        consecutive = 0
        for item in reversed(history):
            if (
                str(item.get("action", "") or "") == "rework_same_work_item"
                and str(item.get("blocker_fingerprint", "") or "") == current_fp
            ):
                consecutive += 1
            else:
                break

        if consecutive + 1 < threshold:
            return decision

        upgraded_summary = (
            f"{decision.summary}\n\n"
            f"[stagnation] The same problem has been flagged {consecutive + 1} "
            f"consecutive times without progress. Escalating to user."
        )
        return GateHarnessDecision(
            action="escalate",
            summary=upgraded_summary,
            target_projection_id=decision.target_projection_id,
            target_projection_ids=list(decision.target_projection_ids),
            source=f"{decision.source}+stagnation_cap",
            blocker_fingerprint=decision.blocker_fingerprint,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fingerprint(action: str, reason: str) -> str:
        if not action and not reason:
            return ""
        joined = f"{action}||{reason.strip()[:240]}"
        return hashlib.sha1(joined.encode("utf-8", errors="replace")).hexdigest()[:16]

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        value = str(text or "").strip()
        if value.startswith("```"):
            value = value.split("\n", 1)[1] if "\n" in value else value[3:]
            if value.endswith("```"):
                value = value[:-3]
        return value.strip()
