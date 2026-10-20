"""Shared visibility rules for persisted session transcript messages.

The database pager and the Office UI renderer must agree on this boundary.
If either side independently classifies transcript kinds, a summary request can
page over rows that the renderer later drops and leave the caller with a cursor
that never advances through the visible timeline.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping


TranscriptDetailLevel = Literal["summary", "full"]

FULL_DETAIL_ONLY_TRANSCRIPT_KINDS: frozenset[str] = frozenset({
    "runtime_v2_user_turn",
    "runtime_v2_intermediate_assistant",
    "runtime_v2_company_assistant",
    "runtime_v2_tool_output",
})


def normalize_transcript_detail_level(value: Any) -> TranscriptDetailLevel:
    return "full" if str(value or "").strip().lower() == "full" else "summary"


def has_iteration_thinking(metadata: Mapping[str, Any] | None) -> bool:
    normalized = dict(metadata or {})
    return (
        normalized.get("kind") == "runtime_v2_intermediate_assistant"
        and bool(str(normalized.get("runtime_thinking_stream_id", "") or "").strip())
    )


def transcript_metadata_visible(
    metadata: Mapping[str, Any] | None,
    *,
    detail_level: TranscriptDetailLevel | str = "summary",
) -> bool:
    """Return whether a persisted transcript row belongs to a detail view.

    ``company_final_turn`` deliberately overrides the kind classification: a
    company role's final reply is the durable user-visible result even when its
    transport kind is normally reserved for the full execution transcript.
    """

    if normalize_transcript_detail_level(detail_level) == "full":
        return True
    normalized_metadata = dict(metadata or {})
    if normalized_metadata.get("company_final_turn") is True:
        return True
    # The UI projects these task-mode rows as thinking-only entries in summary
    # view. Company raw turns keep their existing visibility boundary.
    if has_iteration_thinking(normalized_metadata):
        return True
    kind = str(normalized_metadata.get("kind", "") or "").strip()
    return kind not in FULL_DETAIL_ONLY_TRANSCRIPT_KINDS


def transcript_visibility_sql(
    *,
    detail_level: TranscriptDetailLevel | str,
    metadata_column: str = "metadata",
) -> tuple[str, tuple[str, ...]]:
    """Build the SQLite predicate equivalent of ``transcript_metadata_visible``.

    ``metadata_column`` is supplied only by internal, static query construction;
    callers must not pass user-controlled identifiers.
    """

    if normalize_transcript_detail_level(detail_level) == "full":
        return "", ()
    placeholders = ",".join("?" for _ in FULL_DETAIL_ONLY_TRANSCRIPT_KINDS)
    predicate = (
        "AND (COALESCE(json_extract("
        f"{metadata_column}, '$.company_final_turn'), 0) = 1 "
        "OR (json_extract("
        f"{metadata_column}, '$.kind') = 'runtime_v2_intermediate_assistant' "
        f"AND trim(COALESCE(json_extract({metadata_column}, '$.runtime_thinking_stream_id'), '')) != '') "
        "OR COALESCE(json_extract("
        f"{metadata_column}, '$.kind'), '') NOT IN ({placeholders})) "
    )
    return predicate, tuple(sorted(FULL_DETAIL_ONLY_TRANSCRIPT_KINDS))


def rendered_transcript_metadata_visible(
    metadata: Mapping[str, Any] | None,
    *,
    detail_level: TranscriptDetailLevel | str = "summary",
) -> bool:
    """Apply the visibility marker written by the transcript renderer."""

    if normalize_transcript_detail_level(detail_level) == "full":
        return True
    visibility = str(dict(metadata or {}).get("detail_visibility", "summary") or "summary")
    return visibility.strip().lower() != "full"


def rendered_transcript_visibility_sql(
    *,
    detail_level: TranscriptDetailLevel | str,
    metadata_column: str = "metadata",
) -> str:
    """SQLite predicate equivalent of ``rendered_transcript_metadata_visible``."""

    if normalize_transcript_detail_level(detail_level) == "full":
        return ""
    return (
        " AND lower(COALESCE(json_extract("
        f"{metadata_column}, '$.detail_visibility'), 'summary')) != 'full'"
    )
