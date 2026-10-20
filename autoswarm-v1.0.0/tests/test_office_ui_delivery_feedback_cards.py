from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from opc.core.models import ExecutionCheckpoint, Task
from opc.plugins.office_ui.snapshot_builder import (
    _attach_or_create_checkpoint_card,
    _build_snapshot_checkpoint_meta,
    _checkpoint_meta_targets_task,
    _message_can_host_checkpoint_meta,
)
from opc.plugins.office_ui.ws_handler import WSHandler


class DeliveryFeedbackCardDedupTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_delivery_feedback_meta_carries_waiting_task_id(self) -> None:
        checkpoint = ExecutionCheckpoint(
            checkpoint_id="cp-delivery",
            project_id="p1",
            session_id="delivery-session",
            task_id="delivery-task",
            checkpoint_type="company_delivery_feedback",
            payload={
                "waiting_task_id": "delivery-task",
                "work_item_projection_id": "chief_analyst::delivery",
                "work_item_turn_type": "deliver",
                "feedback_scope": "final",
                "prompt": "Please review.",
                "delivery_package": {"executive_summary": "Ready for review."},
                "delivery_revision": 4,
                "owner_directive_revision": 4,
                "latest_user_directive": "Make the PPT visual.",
                "waiting_work_item_id": "wi-delivery",
                "result_content": "Current delivery content.",
            },
        )
        engine = SimpleNamespace(
            get_latest_pending_checkpoint_for_session=AsyncMock(return_value=checkpoint),
        )
        task = Task(id="delivery-task", title="Delivery", project_id="p1", session_id="delivery-session")

        metadata = await _build_snapshot_checkpoint_meta(engine, task)

        assert metadata is not None
        self.assertEqual(metadata["checkpoint_type"], "company_delivery_feedback")
        self.assertEqual(metadata["checkpoint_id"], "cp-delivery")
        self.assertEqual(metadata["waiting_task_id"], "delivery-task")
        self.assertEqual(metadata["task_id"], "delivery-task")
        self.assertEqual(metadata["delivery_revision"], 4)
        self.assertEqual(metadata["owner_directive_revision"], 4)
        self.assertEqual(metadata["latest_user_directive"], "Make the PPT visual.")
        self.assertEqual(metadata["waiting_work_item_id"], "wi-delivery")
        self.assertEqual(metadata["result_content"], "Current delivery content.")
        self.assertEqual(metadata["summary"], "Ready for review.")
        self.assertEqual([option["id"] for option in metadata["options"]], ["approve", "ignore", "feedback"])

    def test_ws_delivery_feedback_meta_uses_current_result_payload(self) -> None:
        checkpoint = ExecutionCheckpoint(
            checkpoint_id="cp-delivery-current",
            project_id="p1",
            session_id="delivery-session",
            task_id="delivery-task",
            checkpoint_type="company_delivery_feedback",
            payload={
                "waiting_task_id": "delivery-task",
                "waiting_work_item_id": "wi-delivery",
                "work_item_projection_id": "chief_analyst::delivery",
                "work_item_turn_type": "deliver",
                "feedback_scope": "final",
                "prompt": "Please review.",
                "result_content": "This round produced the picture-first PPT.",
                "delivery_revision": 5,
                "owner_directive_revision": 5,
            },
        )

        metadata = WSHandler._build_delivery_feedback_meta(WSHandler.__new__(WSHandler), checkpoint)

        self.assertEqual(metadata["summary"], "This round produced the picture-first PPT.")
        self.assertEqual(metadata["delivery_revision"], 5)
        self.assertEqual(metadata["waiting_work_item_id"], "wi-delivery")
        self.assertEqual([option["id"] for option in metadata["options"]], ["approve", "ignore", "feedback"])

    def test_snapshot_compensation_creates_card_when_no_assistant_message_exists(self) -> None:
        messages = [
            {
                "message_id": "user-only",
                "channel_id": "session:delivery-task",
                "sender": "user",
                "content": "Please revise.",
                "metadata": {},
            },
        ]

        _attach_or_create_checkpoint_card(
            messages,
            channel_id="session:delivery-task",
            checkpoint_meta={
                "checkpoint_type": "company_delivery_feedback",
                "checkpoint_id": "cp-delivery-current",
                "waiting_task_id": "delivery-task",
                "summary": "Current delivery is ready.",
                "prompt": "Review the current delivery.",
            },
        )

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["message_id"], "checkpoint::cp-delivery-current")
        self.assertEqual(messages[-1]["metadata"]["checkpoint_id"], "cp-delivery-current")

    def test_delivery_feedback_checkpoint_targets_only_waiting_task(self) -> None:
        waiting_task = Task(id="delivery-task", title="Delivery", project_id="p1")
        parent_task = Task(id="root-task", title="Root", project_id="p1")
        metadata = {
            "checkpoint_type": "company_delivery_feedback",
            "checkpoint_id": "cp-delivery",
            "waiting_task_id": "delivery-task",
        }

        self.assertTrue(_checkpoint_meta_targets_task(metadata, waiting_task))
        self.assertFalse(_checkpoint_meta_targets_task(metadata, parent_task))
        self.assertTrue(WSHandler._checkpoint_metadata_targets_task(metadata, waiting_task))
        self.assertFalse(WSHandler._checkpoint_metadata_targets_task(metadata, parent_task))

    def test_delivery_feedback_checkpoint_skips_mirrors_and_worker_notifications(self) -> None:
        metadata = {
            "checkpoint_type": "company_delivery_feedback",
            "checkpoint_id": "cp-delivery",
            "waiting_task_id": "delivery-task",
        }
        delivery_message = {
            "sender": "chief_analyst",
            "metadata": {"transcript_kind": "child_task_result"},
        }
        parent_mirror = {
            "sender": "chief_analyst",
            "metadata": {"transcript_kind": "child_result"},
        }
        worker_notification = {
            "sender": "system",
            "metadata": {"kind": "worker_notification"},
        }

        self.assertTrue(_message_can_host_checkpoint_meta(delivery_message, metadata))
        self.assertFalse(_message_can_host_checkpoint_meta(parent_mirror, metadata))
        self.assertFalse(_message_can_host_checkpoint_meta(worker_notification, metadata))
        self.assertTrue(WSHandler._message_can_host_checkpoint_metadata(delivery_message, metadata))
        self.assertFalse(WSHandler._message_can_host_checkpoint_metadata(parent_mirror, metadata))
        self.assertFalse(WSHandler._message_can_host_checkpoint_metadata(worker_notification, metadata))
