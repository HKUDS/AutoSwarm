from __future__ import annotations

import asyncio
import random
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from opc.core.models import SessionMessageRecord, SessionPartRecord, SessionRecord
from opc.core.transcript_visibility import transcript_metadata_visible
from opc.database.store import OPCStore, _SQLiteConnectionAdapter
from opc.plugins.office_ui.chat_store import (
    ChatStore,
    _MessageMatchIndex,
    _MessageMatchState,
)
from opc.plugins.office_ui.snapshot_builder import (
    build_transcript_ui_messages,
    collapse_adjacent_transcript_duplicates,
)
from opc.plugins.office_ui.ws_handler import WSHandler


_WORK_ITEM_RESULT = """Both work items have been successfully dispatched and can execute in parallel.

**Dispatch Summary**

**Work Item 1: AutoSwarm Source Code Architecture Deep-Dive Analysis**
- ID: `1ed5f5f1-ac41-49a1-b1fa-23bbc9adab82`
- Owner: senior_engineer
- Scope: `openopc-source-analysis`
- Output: `/workspace/openopc-architecture-analysis.md`
- Covers: Layered architecture, work-item state machines, collaboration policy, and seat executors.

**Work Item 2: External Multi-Agent Frameworks Architecture Research**
- ID: `d0307208-6b95-44c1-9b51-6bf073bbdcef`
- Owner: senior_engineer
- Scope: `external-frameworks-research`
- Output: `/workspace/external-frameworks-analysis.md`
- Covers: Architecture models, coordination, communication, extensibility, and implementation details.

Both are independent and can execute in parallel."""


class TranscriptStorePaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_pages_expose_iteration_thinking_without_raw_company_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OPCStore(Path(tmpdir) / "tasks.db")
            await store.initialize()
            try:
                await store.save_session(SessionRecord(session_id="thinking", project_id="test-project"))
                for index, kind in enumerate(("runtime_v2_intermediate_assistant", "runtime_v2_company_assistant")):
                    metadata = {"kind": kind, "runtime_thinking_stream_id": f"turn:{index}:thinking", "runtime_thinking": f"thought {index}"}
                    await store.save_session_message(SessionMessageRecord(
                        message_id=f"m{index}", session_id="thinking", role="assistant", metadata=metadata,
                    ))
                    await store.save_session_part(SessionPartRecord(
                        part_id=f"p{index}", message_id=f"m{index}", session_id="thinking",
                        part_type="text", payload={"text": "raw intermediate text"},
                    ))
                page = await store.get_session_transcript_page("thinking", detail_level="summary", limit=1)
                self.assertEqual(page["total_count"], 1)
                rendered = build_transcript_ui_messages(page["messages"], channel_id="session:thinking", task_id="task")
                self.assertEqual(len(rendered), 1)
                self.assertEqual(rendered[0]["content"], "")
                self.assertEqual(rendered[0]["metadata"]["runtime_thinking"], "thought 0")
                full_page = await store.get_session_transcript_page("thinking", detail_level="full")
                full = build_transcript_ui_messages(full_page["messages"], channel_id="session:thinking", task_id="task", detail_level="full")
                self.assertEqual(len(full), 2)
                self.assertTrue(all(row["content"] == "raw intermediate text" for row in full))
                self.assertNotEqual(full[0]["message_id"], rendered[0]["message_id"])
            finally:
                await store.close()

    async def test_summary_page_filters_full_detail_rows_before_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OPCStore(Path(tmpdir) / "tasks.db")
            await store.initialize()
            try:
                session_id = "summary-pagination-session"
                task_id = "summary-pagination-task"
                base = datetime(2026, 7, 13, 12, 0, 0)
                await store.save_session(SessionRecord(
                    session_id=session_id,
                    project_id="test-project",
                    title="Summary pagination",
                    created_at=base,
                    updated_at=base,
                ))

                async def save(
                    message_id: str,
                    offset: int,
                    kind: str,
                    *,
                    company_final_turn: bool = False,
                    summary_flag: bool = False,
                ) -> None:
                    metadata = {"kind": kind}
                    if company_final_turn:
                        metadata["company_final_turn"] = True
                    created_at = base + timedelta(seconds=offset)
                    await store.save_session_message(SessionMessageRecord(
                        message_id=message_id,
                        session_id=session_id,
                        task_id=task_id,
                        role="assistant",
                        agent_id="agent-reviewer",
                        summary_flag=summary_flag,
                        metadata=metadata,
                        created_at=created_at,
                    ))
                    await store.save_session_part(SessionPartRecord(
                        part_id=f"part-{message_id}",
                        message_id=message_id,
                        session_id=session_id,
                        part_type="text",
                        payload={"text": f"content:{message_id}"},
                        created_at=created_at,
                    ))

                await save("summary-old", 0, "top_level_reply")
                hidden_kinds = (
                    "runtime_v2_user_turn",
                    "runtime_v2_intermediate_assistant",
                    "runtime_v2_company_assistant",
                    "runtime_v2_tool_output",
                )
                # More than 8 * page size: post-LIMIT filtering used to return
                # an empty page even though summary-old remained reachable.
                for index in range(24):
                    await save(f"full-only-{index:02d}", index + 1, hidden_kinds[index % len(hidden_kinds)])
                await save("assistant-final", 25, "runtime_v2_assistant")
                await save(
                    "company-final",
                    26,
                    "runtime_v2_company_assistant",
                    company_final_turn=True,
                )
                await save("canonical-result", 27, "child_result")
                await save("compaction-summary", 28, "top_level_reply", summary_flag=True)

                latest = await store.get_session_transcript_page(
                    session_id,
                    limit=2,
                    detail_level="summary",
                )
                self.assertEqual(latest["total_count"], 4)
                self.assertTrue(latest["has_more"])
                self.assertEqual(
                    [item["message"].message_id for item in latest["messages"]],
                    ["company-final", "canonical-result"],
                )
                self.assertEqual(
                    [message["message_id"] for message in build_transcript_ui_messages(
                        latest["messages"],
                        channel_id=f"session:{task_id}",
                        task_id=task_id,
                        detail_level="summary",
                    )],
                    ["company-final", "canonical-result"],
                )

                older = await store.get_session_transcript_page(
                    session_id,
                    limit=2,
                    before_created_at=base + timedelta(seconds=26),
                    before_message_id="company-final",
                    detail_level="summary",
                )
                self.assertEqual(older["total_count"], 4)
                self.assertFalse(older["has_more"])
                self.assertEqual(
                    [item["message"].message_id for item in older["messages"]],
                    ["summary-old", "assistant-final"],
                )

                full = await store.get_session_transcript_page(
                    session_id,
                    limit=2,
                    detail_level="full",
                )
                self.assertEqual(full["total_count"], 28)
                self.assertTrue(full["has_more"])
            finally:
                await store.close()

    async def test_rendered_page_reads_past_empty_and_collapsed_raw_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OPCStore(Path(tmpdir) / "tasks.db")
            await store.initialize()
            try:
                session_id = "rendered-pagination-session"
                task_id = "rendered-pagination-task"
                base = datetime(2026, 7, 13, 13, 0, 0)
                await store.save_session(SessionRecord(
                    session_id=session_id,
                    project_id="test-project",
                    title="Rendered pagination",
                    created_at=base,
                    updated_at=base,
                ))

                async def save(
                    message_id: str,
                    offset: int,
                    kind: str,
                    content: str | None,
                ) -> None:
                    created_at = base + timedelta(seconds=offset)
                    await store.save_session_message(SessionMessageRecord(
                        message_id=message_id,
                        session_id=session_id,
                        task_id=task_id,
                        role="assistant",
                        agent_id="agent-reviewer",
                        metadata={"kind": kind},
                        created_at=created_at,
                    ))
                    if content is not None:
                        await store.save_session_part(SessionPartRecord(
                            part_id=f"part-{message_id}",
                            message_id=message_id,
                            session_id=session_id,
                            part_type="text",
                            payload={"text": content},
                            created_at=created_at,
                        ))

                await save("visible-old", 0, "top_level_reply", "older unique")
                await save("duplicate-low", 1, "top_level_reply", "same result")
                await save("duplicate-high", 2, "child_result", "same result")
                await save("empty-latest", 3, "top_level_reply", None)

                handler = WSHandler.__new__(WSHandler)
                handler.engine = SimpleNamespace(store=store)
                page, total_count, has_more = await handler._load_session_transcript_page(
                    SimpleNamespace(id=task_id, session_id=session_id),
                    limit=2,
                    detail_level="summary",
                )

                self.assertEqual(
                    [message["message_id"] for message in page],
                    ["visible-old", "duplicate-high"],
                )
                self.assertEqual(
                    [message["content"] for message in page],
                    ["older unique", "same result"],
                )
                self.assertGreaterEqual(total_count, 2)
                self.assertFalse(has_more)
            finally:
                await store.close()

    def test_renderer_and_store_share_company_final_visibility(self) -> None:
        self.assertFalse(transcript_metadata_visible(
            {"kind": "runtime_v2_company_assistant"},
            detail_level="summary",
        ))
        self.assertTrue(transcript_metadata_visible(
            {"kind": "runtime_v2_company_assistant", "company_final_turn": True},
            detail_level="summary",
        ))
        self.assertTrue(transcript_metadata_visible(
            {"kind": "runtime_v2_assistant"},
            detail_level="summary",
        ))

    def test_renderer_preserves_structured_result_lineage(self) -> None:
        lineage = {
            "canonical_turn_id": "turn-canonical",
            "turn_id": "turn-execution",
            "result_delivery_id": "delivery-1",
            "source_result_message_id": "source-message-1",
            "source_task_id": "source-task-1",
            "child_session_id": "child-session-1",
            "conversation_turn_id": "conversation-turn-1",
            "execution_turn_id": "execution-turn-1",
            "work_item_projection_id": "architecture",
            "work_item_turn_type": "delivery",
            "runtime_session_id": "runtime-session-1",
        }
        rendered = build_transcript_ui_messages(
            [{
                "message": SimpleNamespace(
                    message_id="result-message-1",
                    role="assistant",
                    agent_id="cto",
                    created_at=datetime(2026, 7, 14, 10, 0, 0),
                    summary_flag=False,
                    metadata={"kind": "child_result", **lineage},
                ),
                "parts": [SimpleNamespace(
                    part_type="text",
                    payload={"text": "Completed architecture analysis."},
                )],
            }],
            channel_id="session:parent-task",
            task_id="parent-task",
        )

        self.assertEqual(len(rendered), 1)
        for key, value in lineage.items():
            self.assertEqual(rendered[0]["metadata"].get(key), value)


class ChatStorePaginationTests(unittest.TestCase):
    @staticmethod
    def _legacy_dedupe(
        store: ChatStore,
        messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Reference implementation retained only for equivalence testing."""
        deduped: list[dict[str, object]] = []
        for message in sorted(messages, key=store._message_timestamp):
            match_index = next(
                (
                    index
                    for index in range(len(deduped) - 1, -1, -1)
                    if store._messages_semantically_match(deduped[index], message)
                ),
                None,
            )
            if match_index is None:
                deduped.append(message)
            else:
                deduped[match_index] = store._merge_duplicate_messages(
                    deduped[match_index],
                    message,
                )
        return deduped

    def test_indexed_dedupe_matches_legacy_semantics(self) -> None:
        randomizer = random.Random(20260713)
        messages: list[dict[str, object]] = []
        content_variants = (
            "Repeated result",
            "Repeated result\n\nVerification: passed",
            "Unique body ",
            "**Narrative heading**: " + ("long body " * 20),
        )
        result_kinds = (
            "child_result",
            "company_role_result",
            "top_level_reply",
            "",
        )
        for index in range(600):
            metadata: dict[str, object] = {}
            if randomizer.random() < 0.55:
                metadata["source"] = "engine"
            result_kind = randomizer.choice(result_kinds)
            if result_kind:
                metadata["transcript_kind"] = result_kind
            # Exercise identity merges which can replace the semantic bucket of
            # an already-indexed row.
            if messages and randomizer.random() < 0.12:
                identity_source = randomizer.choice(messages)
                metadata["ui_message_id"] = identity_source["message_id"]
            content = randomizer.choice(content_variants)
            if content == "Unique body ":
                content += str(index % 31)
            messages.append({
                "message_id": f"random-{index:04d}",
                "channel_id": f"session:{randomizer.randrange(2)}",
                "sender": "user" if randomizer.random() < 0.18 else "assistant",
                "sender_name": "AutoSwarm",
                "content": content,
                # Include zero/negative sentinel values because the historical
                # matcher deliberately treats a zero timestamp as unbounded.
                "created_at": float(randomizer.randrange(-6, 45)) / 3.0,
                "reply_to_id": f"reply-{randomizer.randrange(4)}",
                "mentions": [],
                "metadata": metadata,
            })
        randomizer.shuffle(messages)

        store = ChatStore(None)  # type: ignore[arg-type]
        expected = self._legacy_dedupe(store, messages)
        actual = store._dedupe_messages(messages)
        self.assertEqual(actual, expected)

    def test_work_item_colons_are_not_treated_as_narrative_wrappers(self) -> None:
        normalized = ChatStore._normalize_duplicate_content(_WORK_ITEM_RESULT)
        self.assertEqual(normalized, _WORK_ITEM_RESULT)
        self.assertEqual(ChatStore._normalize_duplicate_content(normalized), normalized)

    def test_duplicate_merge_keeps_work_item_display_content_across_replays(self) -> None:
        store = ChatStore(None)  # type: ignore[arg-type]
        current = {
            "message_id": "runtime-final",
            "channel_id": "session:cto",
            "sender": "assistant",
            "sender_name": "CTO",
            "content": _WORK_ITEM_RESULT,
            "created_at": 1.0,
            "reply_to_id": None,
            "mentions": [],
            "metadata": {
                "source": "engine",
                "transcript_kind": "runtime_v2_company_assistant",
            },
        }
        canonical = {
            **current,
            "message_id": "child-task-result",
            "created_at": 2.0,
            "metadata": {
                "source": "engine",
                "transcript_kind": "child_task_result",
            },
        }

        for _ in range(6):
            current = store._merge_duplicate_messages(current, canonical)
            self.assertEqual(current["content"], _WORK_ITEM_RESULT)

    def test_duplicate_merge_reuses_an_original_unwrapped_surface(self) -> None:
        store = ChatStore(None)  # type: ignore[arg-type]
        body = "Canonical answer " + ("with implementation details. " * 8)
        wrapped = {
            "message_id": "runtime-final",
            "channel_id": "session:answer",
            "sender": "assistant",
            "sender_name": "AutoSwarm",
            "content": f"**Top level answer**: {body}",
            "created_at": 1.0,
            "metadata": {
                "source": "engine",
                "transcript_kind": "runtime_v2_assistant",
            },
        }
        unwrapped = {
            **wrapped,
            "message_id": "top-level-reply",
            "content": body,
            "created_at": 2.0,
            "metadata": {
                "source": "engine",
                "transcript_kind": "top_level_reply",
            },
        }

        merged = store._merge_duplicate_messages(wrapped, unwrapped)
        self.assertEqual(merged["content"], unwrapped["content"])
        self.assertIn(merged["content"], (wrapped["content"], unwrapped["content"]))

    def test_result_delivery_identity_is_namespaced_without_canonical_turn_fallback(self) -> None:
        store = ChatStore(None)  # type: ignore[arg-type]
        existing = {
            "message_id": "raw-final",
            "channel_id": "session:result",
            "sender": "assistant",
            "content": "runtime wording",
            "created_at": 1.0,
            "metadata": {
                "result_delivery_id": "delivery-1",
                "canonical_turn_id": "turn-shared",
            },
        }
        delivered = {
            **existing,
            "message_id": "child-task-result",
            "content": "canonical result wording",
            "metadata": {
                "result_delivery_id": "delivery-1",
                "canonical_turn_id": "turn-shared",
            },
        }
        unrelated_user = {
            **existing,
            "message_id": "user-turn",
            "sender": "user",
            "content": "different user text",
            "metadata": {"canonical_turn_id": "turn-shared"},
        }

        self.assertIn("result_delivery:delivery-1", store._message_identity_keys(existing))
        self.assertTrue(store._messages_semantically_match(existing, delivered))
        self.assertFalse(store._messages_semantically_match(existing, unrelated_user))

    def test_transcript_collapse_keeps_work_item_display_content_across_replays(self) -> None:
        runtime = {
            "message_id": "runtime-final",
            "sender": "assistant",
            "sender_name": "CTO",
            "content": _WORK_ITEM_RESULT,
            "created_at": 1.0,
            "metadata": {"transcript_kind": "runtime_v2_company_assistant"},
        }
        canonical = {
            **runtime,
            "message_id": "child-task-result",
            "created_at": 2.0,
            "metadata": {"transcript_kind": "child_task_result"},
        }

        collapsed = collapse_adjacent_transcript_duplicates([runtime, canonical])
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["content"], _WORK_ITEM_RESULT)

        replayed = collapse_adjacent_transcript_duplicates([runtime, collapsed[0]])
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0]["content"], _WORK_ITEM_RESULT)

    def test_indexed_dedupe_normalizes_long_content_once_per_row(self) -> None:
        class CountingChatStore(ChatStore):
            normalize_calls = 0

            @classmethod
            def _normalize_duplicate_content(cls, content: object) -> str:
                cls.normalize_calls += 1
                return ChatStore._normalize_duplicate_content(content)

        store = CountingChatStore(None)  # type: ignore[arg-type]
        long_content = "x" * 8192
        message_count = 4000
        messages = [
            {
                "message_id": f"scale-{index:05d}",
                "channel_id": "session:scale",
                "sender": "assistant",
                "sender_name": "AutoSwarm",
                "content": long_content,
                "created_at": float(index),
                "reply_to_id": None,
                "mentions": [],
                # Without an engine source these equal-content rows deliberately
                # do not merge; the legacy reverse scan normalized O(n^2) pairs.
                "metadata": {},
            }
            for index in range(message_count)
        ]

        deduped = store._dedupe_messages(messages)
        self.assertEqual(len(deduped), message_count)
        self.assertEqual(CountingChatStore.normalize_calls, message_count)

    def test_timed_backfill_index_does_not_scan_out_of_window_rows(self) -> None:
        store = ChatStore(None)  # type: ignore[arg-type]
        row_count = 1000

        def message(prefix: str, index: int, timestamp: float) -> dict[str, object]:
            return {
                "message_id": f"{prefix}-{index:04d}",
                "channel_id": "session:timed-backfill-scale",
                "sender": "assistant",
                "sender_name": "AutoSwarm",
                "content": "Ordinary engine update",
                "created_at": timestamp,
                "reply_to_id": "same-turn",
                "mentions": [],
                # Intentionally not a result surface: only the exact 2-second
                # ordinary-message window may match these rows.
                "metadata": {"source": "engine"},
            }

        existing = [
            message("existing", index, 100_001.0 + index * 10.0)
            for index in range(row_count)
        ]
        incoming = [
            message("incoming", index, 1.0 + index * 10.0)
            for index in range(row_count)
        ]

        # Prepared-state reference of the former reversed scan. Every incoming
        # row misses and is appended, causing 1000 + ... + 1999 comparisons.
        legacy_rows = list(existing)
        legacy_states = [
            _MessageMatchState.from_message(store, item)
            for item in legacy_rows
        ]
        legacy_matches: list[int | None] = []
        legacy_checks = 0
        for item in incoming:
            candidate = _MessageMatchState.from_message(store, item)
            match_index: int | None = None
            for index in range(len(legacy_states) - 1, -1, -1):
                legacy_checks += 1
                if legacy_states[index].matches(
                    candidate,
                    duplicate_window=store._DUPLICATE_WINDOW_SECONDS,
                ):
                    match_index = index
                    break
            legacy_matches.append(match_index)
            if match_index is None:
                legacy_rows.append(item)
                legacy_states.append(candidate)

        indexed_rows = list(existing)
        timed_index = _MessageMatchIndex(store, indexed_rows)
        indexed_matches: list[int | None] = []
        indexed_checks = 0
        original_matches = _MessageMatchState.matches

        def counted_matches(
            existing_state: _MessageMatchState,
            candidate_state: _MessageMatchState,
            *,
            duplicate_window: float,
        ) -> bool:
            nonlocal indexed_checks
            indexed_checks += 1
            return original_matches(
                existing_state,
                candidate_state,
                duplicate_window=duplicate_window,
            )

        _MessageMatchState.matches = counted_matches
        try:
            for item in incoming:
                candidate = timed_index.prepare(item)
                match_index = timed_index.latest_match(
                    item,
                    prepared_state=candidate,
                )
                indexed_matches.append(match_index)
                if match_index is None:
                    timed_index.append(item, prepared_state=candidate)
        finally:
            _MessageMatchState.matches = original_matches

        self.assertEqual(indexed_matches, legacy_matches)
        self.assertEqual(legacy_checks, 1_499_500)
        self.assertEqual(indexed_checks, 0)

    def test_timed_index_preserves_float_rounding_at_window_boundary(self) -> None:
        store = ChatStore(None)  # type: ignore[arg-type]

        def message(message_id: str, timestamp: float) -> dict[str, object]:
            return {
                "message_id": message_id,
                "channel_id": "session:float-window-boundary",
                "sender": "assistant",
                "sender_name": "AutoSwarm",
                "content": "Boundary update",
                "created_at": timestamp,
                "reply_to_id": "same-turn",
                "mentions": [],
                "metadata": {"source": "engine"},
            }

        existing = message("existing", -1e-300)
        candidate = message("candidate", 2.0)
        self.assertTrue(store._messages_semantically_match(existing, candidate))

        rows = [existing]
        index = _MessageMatchIndex(store, rows)
        self.assertEqual(index.latest_match(candidate), 0)

    def test_backfill_semantic_matches_remain_one_to_one(self) -> None:
        asyncio.run(self._exercise_backfill_semantic_matches_one_to_one())

    async def _exercise_backfill_semantic_matches_one_to_one(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        db = _SQLiteConnectionAdapter(str(Path(tmpdir.name) / "ui-state.db"))
        store = ChatStore(db)  # type: ignore[arg-type]
        await store.initialize()
        channel_id = "session:backfill-scale"
        project_id = "test-project"
        content = "Canonical result " + ("detail " * 1000)
        try:
            for index in range(200):
                await store.insert_message(
                    channel_id,
                    "assistant",
                    "AutoSwarm",
                    content,
                    metadata={
                        "source": "engine",
                        "transcript_kind": "child_result",
                    },
                    message_id=f"existing-result-{index:03d}",
                    project_id=project_id,
                    created_at=float(index + 1),
                )

            backfill = [
                {
                    "message_id": f"backfill-result-{index:03d}",
                    "channel_id": channel_id,
                    "sender": "assistant",
                    "sender_name": "AutoSwarm",
                    "content": content,
                    "created_at": float(index + 1000),
                    "metadata": {
                        "source": "engine",
                        "transcript_kind": "child_result",
                    },
                }
                for index in range(201)
            ]
            inserted = await store.backfill_messages(
                channel_id,
                backfill,
                project_id=project_id,
            )
            self.assertEqual(
                [message["message_id"] for message in inserted],
                ["backfill-result-200"],
            )
            cursor = await db.execute(
                "SELECT COUNT(*) FROM messages WHERE channel_id = ? AND project_id = ?",
                (channel_id, project_id),
            )
            self.assertEqual((await cursor.fetchone())[0], 201)
        finally:
            await db.close()
            tmpdir.cleanup()

    def test_backfill_same_id_repairs_destructively_normalized_content(self) -> None:
        asyncio.run(self._exercise_backfill_same_id_repairs_content())

    async def _exercise_backfill_same_id_repairs_content(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        db = _SQLiteConnectionAdapter(str(Path(tmpdir.name) / "ui-state.db"))
        store = ChatStore(db)  # type: ignore[arg-type]
        await store.initialize()
        channel_id = "session:work-item-repair"
        project_id = "test-project"
        damaged_content = _WORK_ITEM_RESULT[_WORK_ITEM_RESULT.index("AutoSwarm Source"):]
        metadata = {
            "source": "engine",
            "transcript_kind": "child_result",
            "legacy_cache_marker": True,
        }
        authoritative = {
            "message_id": "result-message-1",
            "sender": "assistant",
            "sender_name": "CTO",
            "content": _WORK_ITEM_RESULT,
            "created_at": 1.0,
            "metadata": {
                "source": "engine",
                "transcript_kind": "child_result",
            },
        }
        try:
            await store.insert_message(
                channel_id,
                "assistant",
                "CTO",
                damaged_content,
                metadata=metadata,
                message_id="result-message-1",
                project_id=project_id,
                created_at=1.0,
            )

            repaired = await store.backfill_messages(
                channel_id,
                [authoritative],
                project_id=project_id,
            )
            replayed = await store.backfill_messages(
                channel_id,
                [authoritative],
                project_id=project_id,
            )
            rows = await store.get_channel_messages(
                channel_id,
                limit=20,
                project_id=project_id,
            )

            self.assertEqual([message["message_id"] for message in repaired], ["result-message-1"])
            self.assertEqual(replayed, [])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["content"], _WORK_ITEM_RESULT)
            self.assertTrue(rows[0]["metadata"].get("legacy_cache_marker"))
        finally:
            await db.close()
            tmpdir.cleanup()

    def test_backfill_semantic_duplicate_upgrades_existing_row_in_place(self) -> None:
        asyncio.run(self._exercise_backfill_semantic_duplicate_upgrade())

    async def _exercise_backfill_semantic_duplicate_upgrade(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        db = _SQLiteConnectionAdapter(str(Path(tmpdir.name) / "ui-state.db"))
        store = ChatStore(db)  # type: ignore[arg-type]
        await store.initialize()
        channel_id = "session:semantic-result-repair"
        project_id = "test-project"
        authoritative_content = _WORK_ITEM_RESULT
        authoritative = {
            "message_id": "source-result-message",
            "sender": "cto",
            "sender_name": "CTO",
            "content": authoritative_content,
            "created_at": 2.0,
            "metadata": {
                "source": "engine",
                "transcript_kind": "child_task_result",
            },
        }
        try:
            await store.insert_message(
                channel_id,
                "assistant",
                "CTO",
                f"**Legacy result**: {_WORK_ITEM_RESULT}",
                metadata={
                    "source": "engine",
                    "transcript_kind": "runtime_v2_company_assistant",
                    "legacy_cache_marker": True,
                },
                message_id="mounted-cache-row",
                project_id=project_id,
                created_at=1.0,
            )

            upgraded = await store.backfill_messages(
                channel_id,
                [authoritative],
                project_id=project_id,
            )
            replayed = await store.backfill_messages(
                channel_id,
                [authoritative],
                project_id=project_id,
            )
            rows = await store.get_channel_messages(
                channel_id,
                limit=20,
                project_id=project_id,
            )

            self.assertEqual([message["message_id"] for message in upgraded], ["mounted-cache-row"])
            self.assertEqual(replayed, [])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["message_id"], "mounted-cache-row")
            self.assertEqual(rows[0]["created_at"], 1.0)
            self.assertEqual(rows[0]["content"], authoritative_content)
            self.assertEqual(rows[0]["metadata"].get("transcript_kind"), "child_task_result")
            self.assertTrue(rows[0]["metadata"].get("legacy_cache_marker"))
        finally:
            await db.close()
            tmpdir.cleanup()

    def test_summary_cache_page_filters_before_raw_fetch_limit(self) -> None:
        asyncio.run(self._exercise_summary_cache_page())

    async def _exercise_summary_cache_page(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        db = _SQLiteConnectionAdapter(str(Path(tmpdir.name) / "ui-state.db"))
        store = ChatStore(db)  # type: ignore[arg-type]
        await store.initialize()
        channel_id = "session:summary-cache-task"
        project_id = "test-project"

        async def insert(message_id: str, timestamp: float, visibility: str) -> None:
            await store.insert_message(
                channel_id,
                "agent-reviewer",
                "Reviewer",
                f"content:{message_id}",
                metadata={"detail_visibility": visibility},
                message_id=message_id,
                project_id=project_id,
                created_at=timestamp,
            )

        try:
            await insert("summary-old", 1.0, "summary")
            for index in range(24):
                await insert(f"full-only-{index:02d}", float(index + 2), "full")
            await insert("summary-new", 26.0, "summary")

            page = await store.get_channel_messages_page(
                channel_id,
                limit=2,
                detail_level="summary",
                project_id=project_id,
            )
            self.assertEqual(
                [message["message_id"] for message in page],
                ["summary-old", "summary-new"],
            )
            self.assertEqual(
                await store.get_channel_visible_message_count(
                    channel_id,
                    project_id=project_id,
                    detail_level="summary",
                ),
                2,
            )
            self.assertEqual(
                await store.get_channel_visible_message_count(
                    channel_id,
                    project_id=project_id,
                    detail_level="full",
                ),
                26,
            )

            older = await store.get_channel_messages_page(
                channel_id,
                limit=2,
                before_timestamp=26.0,
                before_message_id="summary-new",
                detail_level="summary",
                project_id=project_id,
            )
            self.assertEqual([message["message_id"] for message in older], ["summary-old"])
        finally:
            await db.close()
            tmpdir.cleanup()

    def test_cache_page_dedupes_before_paging_and_keeps_ui_only_rows(self) -> None:
        asyncio.run(self._exercise_cache_page_with_ui_only_rows())

    async def _exercise_cache_page_with_ui_only_rows(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        db = _SQLiteConnectionAdapter(str(Path(tmpdir.name) / "ui-state.db"))
        store = ChatStore(db)  # type: ignore[arg-type]
        await store.initialize()
        channel_id = "session:mixed-cache-task"
        project_id = "test-project"

        async def insert(
            message_id: str,
            timestamp: float,
            content: str,
            metadata: dict[str, object],
        ) -> None:
            await store.insert_message(
                channel_id,
                "assistant",
                "AutoSwarm",
                content,
                metadata=metadata,
                message_id=message_id,
                project_id=project_id,
                created_at=timestamp,
            )

        try:
            # These two UI-owned rows have no authoritative transcript row, but
            # must still contribute to the page cursor, total, and has_more.
            await insert(
                "approval-card",
                1.0,
                "",
                {
                    "checkpoint_id": "checkpoint-1",
                    "checkpoint_type": "tool_approval",
                    "checkpoint_status": "pending",
                },
            )
            await insert(
                "legacy-notice",
                2.0,
                "Legacy execution notice",
                {"kind": "legacy_notice"},
            )

            # More than the old ``limit * 8`` lookahead collapses to one final
            # result surface.  Raw-row pagination therefore used to hide both
            # older UI-only rows and incorrectly report the end of history.
            for index in range(24):
                await insert(
                    f"result-surface-{index:02d}",
                    float(index + 3),
                    "Canonical child result",
                    {
                        "source": "engine",
                        "transcript_kind": "child_result",
                        "detail_visibility": "summary",
                    },
                )
            await insert(
                "latest-message",
                27.0,
                "Latest committed reply",
                {"detail_visibility": "summary"},
            )
            for index in range(20):
                await insert(
                    f"full-only-{index:02d}",
                    float(index + 28),
                    f"Runtime row {index}",
                    {"detail_visibility": "full"},
                )

            page = await store.get_channel_messages_page_info(
                channel_id,
                limit=2,
                detail_level="summary",
                project_id=project_id,
            )
            self.assertEqual(page["total_count"], 4)
            self.assertTrue(page["has_more"])
            self.assertEqual(
                [message["message_id"] for message in page["messages"]],
                ["result-surface-00", "latest-message"],
            )

            result_message = page["messages"][0]
            older = await store.get_channel_messages_page_info(
                channel_id,
                limit=2,
                before_timestamp=result_message["created_at"],
                before_message_id=result_message["message_id"],
                detail_level="summary",
                project_id=project_id,
            )
            self.assertFalse(older["has_more"])
            self.assertEqual(older["total_count"], 4)
            self.assertEqual(
                [message["message_id"] for message in older["messages"]],
                ["approval-card", "legacy-notice"],
            )

            compatible = await store.get_channel_messages_page(
                channel_id,
                limit=2,
                detail_level="summary",
                project_id=project_id,
            )
            self.assertEqual(compatible, page["messages"])
        finally:
            await db.close()
            tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
