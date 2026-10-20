"""Utilities for normalizing worker-facing message envelopes."""

from __future__ import annotations

from typing import Any


MESSAGE_CLASSES = {"chat", "protocol", "notification"}
# Protocol types that messages may carry. Ten previously-declared values
# (permission_*, plan_approval_*, dependency_*, approval_reply, cross_team_*)
# were never actually written by any code path and have been removed along
# with the matching CommsSemanticType enum values. See
# plans/task-cleanup-dead-comms.md.
_PROTOCOL_TYPES = {
    "approval_request",
    "shutdown_request",
    "shutdown_response",
    "ack",
}
_NOTIFICATION_KINDS = {
    "idle",
    "task_complete",
    "blocked",
    "handoff_ready",
    "completion",
    "status_digest",
    "permission_needed",
    "error",
}

_SEMANTIC_PROTOCOL_MAP = {
    "approval_request": "approval_request",
}

_SEMANTIC_NOTIFICATION_MAP = {
    "idle_notification": "idle",
    "handoff_ready": "handoff_ready",
    "work_item_result": "task_complete",
    "blocker": "blocked",
    "completion": "completion",
    "status_digest": "status_digest",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_choice(value: Any, allowed: set[str]) -> str:
    normalized = _clean_text(value).lower()
    return normalized if normalized in allowed else ""


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return None


def normalize_worker_envelope_metadata(
    metadata: dict[str, Any] | None = None,
    *,
    msg_type: str = "",
    semantic_type: str = "",
    transport_kind: str = "",
    from_agent: str = "",
    reply_needed: bool = False,
    worker_id: str = "",
    task_id: str = "",
    projection_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Return additive worker-envelope metadata for chat/protocol/notification routing."""
    merged = dict(metadata or {})

    protocol_type = _clean_choice(merged.get("protocol_type"), _PROTOCOL_TYPES)
    if not protocol_type:
        protocol_type = _SEMANTIC_PROTOCOL_MAP.get(_clean_text(semantic_type).lower(), "")
    if not protocol_type and _clean_text(msg_type).lower() == "ack":
        protocol_type = "ack"

    notification_kind = _clean_choice(merged.get("notification_kind"), _NOTIFICATION_KINDS)
    if not notification_kind:
        notification_kind = _SEMANTIC_NOTIFICATION_MAP.get(_clean_text(semantic_type).lower(), "")
    resident_status = _clean_text(merged.get("resident_status")).lower()
    if not notification_kind and resident_status in {"idle", "blocked"}:
        notification_kind = resident_status
    if not notification_kind and _clean_text(merged.get("status")).lower() in {"failed", "error"}:
        notification_kind = "error"

    message_class = _clean_choice(merged.get("message_class"), MESSAGE_CLASSES)
    if not message_class:
        if protocol_type:
            message_class = "protocol"
        elif notification_kind:
            message_class = "notification"
        else:
            message_class = "chat"

    actionable = _coerce_bool(merged.get("actionable"))
    if actionable is None:
        actionable = message_class != "notification"

    resolved_worker_id = (
        _clean_text(merged.get("worker_id"))
        or _clean_text(worker_id)
        or _clean_text(merged.get("member_session_id"))
        or _clean_text(merged.get("runtime_session_id"))
        or _clean_text(from_agent)
    )

    origin_task_id = _clean_text(merged.get("origin_task_id")) or _clean_text(task_id)
    origin_projection_id = _clean_text(merged.get("origin_projection_id")) or _clean_text(projection_id)
    origin_session_id = _clean_text(merged.get("origin_session_id")) or _clean_text(session_id)

    merged.update(
        {
            "message_class": message_class,
            "protocol_type": protocol_type or None,
            "notification_kind": notification_kind or None,
            "actionable": bool(actionable),
            "worker_id": resolved_worker_id,
            "origin_task_id": origin_task_id or None,
            "origin_projection_id": origin_projection_id or None,
            "origin_session_id": origin_session_id or None,
        }
    )
    if reply_needed and message_class == "notification":
        merged["actionable"] = True
    if _clean_text(transport_kind):
        merged.setdefault("transport_kind", _clean_text(transport_kind).lower())
    if _clean_text(semantic_type):
        merged.setdefault("semantic_type", _clean_text(semantic_type).lower())
    return merged


def envelope_fields_from_message(message: dict[str, Any]) -> dict[str, Any]:
    metadata = normalize_worker_envelope_metadata(
        dict(message.get("metadata", {}) or {}),
        msg_type=_clean_text(message.get("msg_type")),
        semantic_type=_clean_text(message.get("semantic_type")),
        transport_kind=_clean_text(message.get("transport_kind")),
        from_agent=_clean_text(message.get("from_agent") or message.get("from")),
        reply_needed=bool(message.get("reply_needed")),
        worker_id=_clean_text(message.get("worker_id")),
        task_id=_clean_text(message.get("origin_task_id") or message.get("task_id")),
        projection_id=_clean_text(message.get("origin_projection_id") or message.get("projection_id")),
        session_id=_clean_text(message.get("origin_session_id") or message.get("session_id")),
    )
    return {
        "message_class": metadata.get("message_class", "chat"),
        "protocol_type": metadata.get("protocol_type"),
        "notification_kind": metadata.get("notification_kind"),
        "actionable": bool(metadata.get("actionable", True)),
        "worker_id": metadata.get("worker_id"),
        "origin_task_id": metadata.get("origin_task_id"),
        "origin_projection_id": metadata.get("origin_projection_id"),
        "origin_session_id": metadata.get("origin_session_id"),
        "metadata": metadata,
    }


def classify_worker_message(message: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with normalized worker-envelope fields."""
    merged = dict(message)
    envelope = envelope_fields_from_message(message)
    merged.update({k: v for k, v in envelope.items() if k != "metadata"})
    merged["metadata"] = envelope["metadata"]
    return merged


def worker_message_is_actionable(message: dict[str, Any]) -> bool:
    envelope = envelope_fields_from_message(message)
    return bool(envelope.get("actionable", True)) and envelope.get("message_class") in {"chat", "protocol"}
