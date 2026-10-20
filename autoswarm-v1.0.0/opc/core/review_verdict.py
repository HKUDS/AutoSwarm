"""Lossless review JSON parsing shared by native and external execution.

Parsing transports the reviewer's decision; validation checks whether a
rejected worker would receive a reason. Neither function judges the work.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


_LABELS = {
    **dict.fromkeys(("approve", "approved", "pass", "passed", "accept", "accepted"), "approve"),
    **dict.fromkeys(("reject", "rejected", "fail", "failed", "rework"), "reject"),
}
_BARE_DECISIONS = set(_LABELS) | {
    "", "no", "nope", "needs changes", "not approved", "n/a", "none", "null",
    "拒绝", "驳回", "不通过", "未通过", "返工", "需要修改", "无", "暂无",
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _items(value: Any) -> list[str]:
    return [text for item in value if (text := _text(item))] if isinstance(value, list) else []


def normalize_review_verdict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        label = _LABELS.get(value.strip().lower())
        return {"label": label, "summary": value.strip()} if label else {}
    if not isinstance(value, Mapping):
        return {}
    raw = next((value[key] for key in ("review_verdict", "verdict", "decision", "status", "label") if value.get(key)), "")
    label = _LABELS.get(_text(raw).lower())
    if not label:
        return {}
    return {
        "label": label,
        "summary": _text(value.get("summary")),
        "blocking_issues": _items(value.get("blocking_issues")),
        "followups": _items(value.get("followups")),
    }


def review_verdict_from_object(candidate: Any) -> dict[str, Any]:
    """Accept flat envelopes, nested envelopes and legacy verdict aliases."""
    if not isinstance(candidate, Mapping):
        return {}
    for key in ("review_verdict", "structured_review_verdict"):
        if key not in candidate:
            continue
        inner = candidate[key]
        if isinstance(inner, Mapping):
            normalized = normalize_review_verdict(inner)
        else:
            # Keep sibling fields instead of reducing the envelope to a label.
            normalized = normalize_review_verdict({**candidate, "review_verdict": inner})
        if normalized:
            return normalized
    return normalize_review_verdict(candidate)


def parse_review_verdict(content: str, artifacts: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Prefer explicit artifacts, then the first valid review JSON object.

    Only enrich a label-only artifact from text when both decisions agree.
    Never combine contradictory decisions or unrelated JSON objects.
    """
    artifact_verdict = review_verdict_from_object(artifacts or {})
    if artifact_verdict and not review_feedback_error(artifact_verdict):
        return artifact_verdict
    decoder = json.JSONDecoder()
    text = str(content or "")
    start = text.find("{")
    while start != -1:
        try:
            candidate, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        verdict = review_verdict_from_object(candidate)
        if verdict:
            if not artifact_verdict:
                return verdict
            if verdict["label"] == artifact_verdict["label"] and not review_feedback_error(verdict):
                return verdict
            return artifact_verdict
        start = text.find("{", start + max(consumed, 1))
    return artifact_verdict


def review_feedback_error(verdict: Mapping[str, Any]) -> str:
    """A reject needs a reason or a concrete blocking change, not a bare label.

    There is no minimum length or language/quality heuristic. Non-blocking
    followups alone cannot explain why the deliverable was rejected.
    """
    if verdict.get("label") != "reject":
        return ""
    candidates = [_text(verdict.get("summary")), *_items(verdict.get("blocking_issues"))]
    if any(text.casefold().strip(" \t\r\n.!?。！？:：`*#_-") not in _BARE_DECISIONS for text in candidates):
        return ""
    return (
        "REVIEW_REJECT_FEEDBACK_MISSING: You returned reject without a reason or a "
        "specific blocking change. Regenerate your review JSON with a concrete "
        "summary and/or blocking_issues describing what the worker must correct. "
        "A bare reject or non-blocking followups is insufficient. The worker has "
        "not been sent for rework; this error belongs to you, the reviewer."
    )
