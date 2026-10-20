"""Invariant: a completed turn's top-level reply must be visible in the UI channel.

Project 000 forensics (2026-07-07 19:21 / 20:27): the engine recorded the
assistant reply in session_messages, but the reply never appeared in the
ui_state messages table, so the user stared at an empty conversation. The UI
projection of engine replies runs only as a post-turn transcript sync inside
_process_session_message; if that step is starved, cancelled, or misses the
row, nothing reconciles the channel afterwards.

These tests drive WSHandler._process_session_message against a real OPCStore
and a real ChatStore with the engine's process_message mocked to behave like
the incident turn (records the transcript row, returns the reply text).
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite

from opc.core.models import SessionMessageRecord, SessionPartRecord, Task
from opc.database.store import OPCStore
from opc.plugins.office_ui.chat_store import ChatStore
from opc.plugins.office_ui.event_adapter import EventAdapter
from opc.plugins.office_ui.ws_handler import WSHandler

PROJECT_ID = "proj-reply"
REPLY_TEXT = (
    "A legacy company runtime run was found for this session. "
    "Legacy runs are read-only and cannot be resumed under the work-item runtime."
)


class ReplyProjectionInvariantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)

        self.store = OPCStore(root / "tasks.db")
        await self.store.initialize()
        self.addAsyncCleanup(self.store.close)

        db = await aiosqlite.connect(root / "ui_state.db")
        self.chat_store = ChatStore(db)
        await self.chat_store.initialize()
        self.addAsyncCleanup(db.close)

        self.session_id = str(uuid.uuid4())
        self.task_id = str(uuid.uuid4())
        await self.store.save_task(Task(
            id=self.task_id,
            title="Competitive analysis",
            project_id=PROJECT_ID,
            session_id=self.session_id,
        ))

        self.engine = MagicMock()
        self.engine.store = self.store
        self.engine.project_id = PROJECT_ID
        self.engine.memory = None
        self.engine.get_latest_pending_checkpoint_for_session = AsyncMock(return_value=None)
        self.engine.get_pending_checkpoints_for_session = AsyncMock(return_value=[])
        self.engine.process_message = AsyncMock(side_effect=self._engine_turn)

        self.handler = WSHandler(self.engine, MagicMock(), self.chat_store, EventAdapter())
        self.broadcasts: list[dict] = []
        self.handler.broadcast = AsyncMock(side_effect=lambda msg: self.broadcasts.append(msg))
        self.handler._send_ack = AsyncMock()

    async def _engine_turn(self, content: str, **_kwargs: Any) -> str:
        """Mimic the incident turn: persist user + assistant transcript rows,
        return the reply text (engine's process_message contract)."""
        for role, text, kind in (
            ("user", content, "top_level_user_turn"),
            ("assistant", REPLY_TEXT, "top_level_reply"),
        ):
            record = SessionMessageRecord(
                session_id=self.session_id,
                role=role,
                metadata={
                    "project_id": PROJECT_ID,
                    "session_id": self.session_id,
                    "interface": "office_ui",
                    "kind": kind,
                },
            )
            await self.store.save_session_message(record)
            await self.store.save_session_part(SessionPartRecord(
                message_id=record.message_id,
                session_id=self.session_id,
                part_type="text",
                payload={"text": text},
            ))
        return REPLY_TEXT

    async def _channel_contents(self) -> list[tuple[str, str]]:
        channel_id = f"session:{self.task_id}"
        cursor = await self.chat_store._db.execute(
            "SELECT sender, content FROM messages WHERE channel_id = ? AND project_id = ? "
            "ORDER BY timestamp",
            (channel_id, PROJECT_ID),
        )
        return [(str(row[0]), str(row[1])) for row in await cursor.fetchall()]

    async def test_reply_reaches_ui_channel_after_turn(self) -> None:
        await self.handler._process_session_message(
            self.task_id,
            "你的交付文件在哪里？",
            session_id=self.session_id,
        )

        rows = await self._channel_contents()
        assistant_rows = [content for sender, content in rows if sender != "user"]
        self.assertTrue(
            any(REPLY_TEXT.split(".")[0] in content for content in assistant_rows),
            f"assistant reply missing from UI channel; channel rows: {rows!r}",
        )

    async def test_reply_projected_even_if_transcript_sync_misses(self) -> None:
        """The last-resort projection must cover sync failures (starvation,
        cancellation, mapping defects) — the incident's exact shape."""
        self.handler._sync_task_transcript_messages = AsyncMock(return_value=0)

        await self.handler._process_session_message(
            self.task_id,
            "你的交付文件在哪里？",
            session_id=self.session_id,
        )

        rows = await self._channel_contents()
        assistant_rows = [content for sender, content in rows if sender != "user"]
        self.assertTrue(
            any(REPLY_TEXT.split(".")[0] in content for content in assistant_rows),
            f"assistant reply missing from UI channel; channel rows: {rows!r}",
        )
        # And it must not double-insert when the sync did work: run a normal
        # turn in the same channel and count copies of its reply.
        self.handler._sync_task_transcript_messages = WSHandler._sync_task_transcript_messages.__get__(self.handler)
        await self.handler._process_session_message(
            self.task_id,
            "再问一次",
            session_id=self.session_id,
        )
        rows = await self._channel_contents()
        copies = [content for sender, content in rows if sender != "user" and REPLY_TEXT[:40] in content]
        self.assertLessEqual(len(copies), 2)


if __name__ == "__main__":
    unittest.main()
