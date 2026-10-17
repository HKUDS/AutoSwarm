from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from opc.plugins.office_ui.snapshot_builder import (
    _sanitize_ui_message_dict,
    _strip_trailing_verification_footer,
    _transcript_item_to_ui_message,
)


def test_strip_trailing_verification_footer_removes_only_footer() -> None:
    content = (
        "I can help with many executable tasks.\n\n"
        "Verification: not required because no code edits or risky runtime actions were detected."
    )

    body, footer = _strip_trailing_verification_footer(content)

    assert body == "I can help with many executable tasks."
    assert footer == "Verification: not required because no code edits or risky runtime actions were detected."


def test_strip_trailing_verification_footer_keeps_footer_only_message() -> None:
    content = "Verification: verified by verify."

    body, footer = _strip_trailing_verification_footer(content)

    assert body == content
    assert footer is None


def test_thinking_only_tool_iteration_survives_snapshot_backfill() -> None:
    item = {
        "message": SimpleNamespace(
            message_id="thinking-only", role="assistant", agent_id="", created_at=datetime.now(), summary_flag=False,
            metadata={"kind": "runtime_v2_intermediate_assistant", "canonical_turn_id": "turn",
                      "ui_message_id": "runtime-v2-intermediate-assistant:turn:iter:2",
                      "runtime_thinking_stream_id": "turn:iter:2:thinking"},
        ),
        "parts": [SimpleNamespace(part_type="thinking", payload={"text": "thinking round two"})],
    }
    mapped = _transcript_item_to_ui_message(item, channel_id="session:t1", task_id="t1")
    assert mapped is not None
    assert mapped["metadata"]["runtime_thinking"] == "thinking round two"
    assert mapped["metadata"]["runtime_thinking_stream_id"] == "turn:iter:2:thinking"
    assert mapped["metadata"]["runtime_thinking_only"] is True
    assert mapped["metadata"]["ui_message_id"] == mapped["message_id"]
    full = _transcript_item_to_ui_message(item, channel_id="session:t1", task_id="t1", detail_level="full")
    assert full is not None
    assert full["message_id"] != mapped["message_id"]


def test_sanitize_ui_message_dict_strips_assistant_footer_and_preserves_metadata() -> None:
    message = {
        "message_id": "m1",
        "channel_id": "session:t1",
        "sender": "assistant",
        "sender_name": "AutoSwarm",
        "content": (
            "Completed the task.\n\n"
            "Verification: verified by verify."
        ),
        "created_at": 123.0,
        "metadata": {"source": "engine", "role": "assistant"},
    }

    sanitized = _sanitize_ui_message_dict(message)

    assert sanitized["content"] == "Completed the task."
    assert sanitized["metadata"]["verification_verdict"] == "Verification: verified by verify."
    assert sanitized["metadata"]["role"] == "assistant"


def test_transcript_item_to_ui_message_hides_runtime_internal_user_turns() -> None:
    item = {
        "message": SimpleNamespace(
            message_id="runtime-user-1",
            role="user",
            agent_id="",
            created_at=datetime.now(),
            summary_flag=False,
            metadata={"kind": "runtime_v2_user_turn"},
        ),
        "parts": [
            SimpleNamespace(
                part_type="text",
                payload={"text": "## Task\nWhat tasks can you help with?"},
            ),
        ],
    }

    assert _transcript_item_to_ui_message(item, channel_id="session:t1", task_id="t1") is None


def test_transcript_item_to_ui_message_strips_trailing_verification_footer() -> None:
    item = {
        "message": SimpleNamespace(
            message_id="reply-1",
            role="assistant",
            agent_id="",
            created_at=datetime.now(),
            summary_flag=False,
            metadata={"kind": "top_level_reply"},
        ),
        "parts": [
            SimpleNamespace(
                part_type="text",
                payload={
                    "text": (
                        "Here is the answer.\n\n"
                        "Verification: not required because no code edits or risky runtime actions were detected."
                    ),
                },
            ),
        ],
    }

    mapped = _transcript_item_to_ui_message(item, channel_id="session:t1", task_id="t1")

    assert mapped is not None
    assert mapped["content"] == "Here is the answer."
    assert mapped["metadata"]["verification_verdict"].startswith("Verification:")
