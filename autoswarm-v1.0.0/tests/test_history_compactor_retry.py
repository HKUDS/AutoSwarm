from __future__ import annotations

import json
import unittest

from opc.layer5_memory.history_compactor import HistoryCompactor


class _OverflowLLM:
    def __init__(self) -> None:
        self.calls = 0

    def is_context_overflow_error(self, error: Exception) -> bool:
        return "prompt too long" in str(error).lower()

    async def simple_chat(self, prompt: str, system: str | None = None, task_type: str | None = None) -> str:
        _ = (system, task_type)
        self.calls += 1
        payload = json.loads(prompt)
        messages = list(payload.get("messages", []) or [])
        if self.calls == 1:
            raise RuntimeError("prompt too long")
        return json.dumps(
            {
                "history_summary": f"summarized {len(messages)} messages",
                "memory_summary": "## Primary Goal\n- Continue safely.",
            },
            ensure_ascii=False,
        )


class _SingleMessageOverflowLLM:
    def __init__(self) -> None:
        self.calls = 0

    def is_context_overflow_error(self, error: Exception) -> bool:
        return "prompt too long" in str(error).lower()

    async def simple_chat(self, prompt: str, system: str | None = None, task_type: str | None = None) -> str:
        _ = (system, task_type)
        self.calls += 1
        if len(prompt) > 4500:
            raise RuntimeError("prompt too long")
        payload = json.loads(prompt)
        messages = list(payload.get("messages", []) or [])
        return json.dumps(
            {
                "history_summary": f"single-message summarized {len(messages)} messages",
                "memory_summary": "## Primary Goal\n- Continue safely.",
            },
            ensure_ascii=False,
        )


class HistoryCompactorRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_simple_chat_retry_truncates_messages_after_overflow(self) -> None:
        llm = _OverflowLLM()
        compactor = HistoryCompactor(
            llm=llm,
            store=object(),
            memory_manager=object(),
        )
        result = await compactor._summarize_session(
            project_id="proj1",
            session_id="sess1",
            messages=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
                {"role": "assistant", "content": "d"},
                {"role": "user", "content": "e"},
            ],
            existing_memory="",
            existing_summary="",
        )

        self.assertEqual(llm.calls, 2)
        self.assertIn("summarized", result["history_summary"])
        self.assertIn("Primary Goal", result["memory_summary"])

    async def test_simple_chat_retry_truncates_single_huge_message_content(self) -> None:
        llm = _SingleMessageOverflowLLM()
        compactor = HistoryCompactor(
            llm=llm,
            store=object(),
            memory_manager=object(),
        )
        result = await compactor._summarize_session(
            project_id="proj1",
            session_id="sess1",
            messages=[
                {"role": "assistant", "content": "x" * 5000},
            ],
            existing_memory="",
            existing_summary="",
        )

        self.assertGreaterEqual(llm.calls, 2)
        self.assertIn("single-message summarized", result["history_summary"])
