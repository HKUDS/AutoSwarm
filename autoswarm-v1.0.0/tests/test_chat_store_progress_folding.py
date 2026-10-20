from __future__ import annotations

import unittest

from opc.plugins.office_ui.chat_store import ChatStore


class ChatStoreProgressFoldingTests(unittest.TestCase):
    def test_stream_fold_preserves_first_timestamp(self) -> None:
        first = {
            "timestamp": 1_700_000_000.0,
            "type": "thinking",
            "summary": "Thinking",
            "detail": "Need ",
            "turn_id": "runtime-1:1",
            "item_id": "runtime-1:1:thinking",
            "seq": 1,
        }
        deltas = [
            {
                "timestamp": 1_700_000_000.1,
                "type": "thinking",
                "summary": "Thinking",
                "detail": "more ",
                "turn_id": "runtime-1:1",
                "item_id": "runtime-1:1:thinking",
                "seq": 2,
            },
            {
                "timestamp": 1_700_000_000.2,
                "type": "thinking",
                "summary": "Thinking",
                "detail": "context",
                "turn_id": "runtime-1:1",
                "item_id": "runtime-1:1:thinking",
                "seq": 3,
            },
        ]

        folded = ChatStore._fold_progress_entries([first], deltas)

        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]["timestamp"], first["timestamp"])
        self.assertEqual(folded[0]["detail"], "Need more context")
        self.assertEqual(folded[0]["seq"], 3)
        # Folding builds a replacement row and must not mutate the persisted
        # value supplied by the caller.
        self.assertEqual(first["detail"], "Need ")

    def test_stream_fold_within_one_batch_keeps_creation_timestamp(self) -> None:
        folded = ChatStore._fold_progress_entries(
            [],
            [
                {
                    "timestamp": 10.0,
                    "type": "assistant",
                    "summary": "Part one",
                    "detail": "Part one ",
                    "turn_id": "runtime-2:1",
                    "stream_id": "runtime-2:1:assistant",
                    "seq": 1,
                },
                {
                    "timestamp": 11.0,
                    "type": "assistant",
                    "summary": "part two",
                    "detail": "part two",
                    "turn_id": "runtime-2:1",
                    "stream_id": "runtime-2:1:assistant",
                    "seq": 2,
                },
            ],
        )

        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]["timestamp"], 10.0)
        self.assertEqual(folded[0]["detail"], "Part one part two")


if __name__ == "__main__":
    unittest.main()
