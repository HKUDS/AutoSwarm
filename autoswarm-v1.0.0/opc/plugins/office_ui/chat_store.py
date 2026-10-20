"""Chat Store — channels, messages, and task progress persistence for the office-UI plugin.

This is the ONLY genuinely new persistence (AutoSwarm has no channel concept).
Channel/message format uses snake_case to match what collabSync.ts expects.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import math
import re
import sqlite3
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiosqlite
from opc.core.transcript_visibility import rendered_transcript_visibility_sql
from opc.layer3_agent.adapters.codex_adapter import CodexAdapter

_LOCKED_ERROR_MARKERS = ("database is locked", "database table is locked")
_WRITE_RETRY_ATTEMPTS = 3
_WRITE_RETRY_BASE_DELAY_SECONDS = 0.25


def _is_locked_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).lower() for marker in _LOCKED_ERROR_MARKERS
    )


@dataclass(frozen=True)
class _MessageMatchState:
    """Prepared fields used by semantic message de-duplication.

    Transcript rows can contain several kilobytes of Markdown.  Preparing the
    normalized content once prevents every candidate comparison from repeating
    that work.
    """

    channel_id: str
    identity_keys: frozenset[str]
    role_bucket: str
    normalized_content: str
    reply_to_id: str
    is_result_surface: bool
    has_engine_source: bool
    timestamp: float

    @classmethod
    def from_message(
        cls,
        owner: Any,
        message: dict[str, Any],
    ) -> _MessageMatchState:
        return cls(
            channel_id=str(message.get("channel_id", "") or ""),
            identity_keys=frozenset(owner._message_identity_keys(message)),
            role_bucket=owner._message_role_bucket(message),
            normalized_content=owner._normalize_duplicate_content(message.get("content", "")),
            reply_to_id=str(message.get("reply_to_id", "") or ""),
            is_result_surface=owner._message_is_result_surface(message),
            has_engine_source=owner._message_has_engine_source(message),
            timestamp=owner._message_timestamp(message),
        )

    def matches(self, candidate: _MessageMatchState, *, duplicate_window: float) -> bool:
        if self.channel_id != candidate.channel_id:
            return False
        if self.identity_keys & candidate.identity_keys:
            return True
        if self.role_bucket != candidate.role_bucket:
            return False
        if self.normalized_content != candidate.normalized_content:
            return False
        both_result_surfaces = self.is_result_surface and candidate.is_result_surface
        if not both_result_surfaces and self.reply_to_id != candidate.reply_to_id:
            return False
        if not (self.has_engine_source or candidate.has_engine_source):
            return False
        if (
            not both_result_surfaces
            and self.timestamp
            and candidate.timestamp
            and abs(self.timestamp - candidate.timestamp) > duplicate_window
        ):
            return False
        return True


@dataclass
class _TimestampRangeNode:
    """Treap node augmented with the greatest timeline index below it."""

    key: tuple[float, int]
    priority: int
    left: _TimestampRangeNode | None = None
    right: _TimestampRangeNode | None = None
    max_index: int = -1

    def __post_init__(self) -> None:
        self.max_index = self.key[1]


class _TimestampRangeTree:
    """Dynamic timestamp range -> latest timeline index map.

    A deterministic treap avoids depending on insertion order while supporting
    insert, delete, and inclusive range maximum in expected O(log n).
    """

    _MASK_64 = (1 << 64) - 1

    def __init__(self) -> None:
        self._root: _TimestampRangeNode | None = None

    @classmethod
    def _priority(cls, index: int) -> int:
        # SplitMix64 is a bijective mixer for the practical index range, giving
        # deterministic pseudo-random treap priorities without global RNG state.
        value = (index + 0x9E3779B97F4A7C15) & cls._MASK_64
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & cls._MASK_64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & cls._MASK_64
        return value ^ (value >> 31)

    @staticmethod
    def _refresh(node: _TimestampRangeNode | None) -> None:
        if node is None:
            return
        node.max_index = max(
            node.key[1],
            node.left.max_index if node.left is not None else -1,
            node.right.max_index if node.right is not None else -1,
        )

    @classmethod
    def _split(
        cls,
        node: _TimestampRangeNode | None,
        key: tuple[float, float | int],
    ) -> tuple[_TimestampRangeNode | None, _TimestampRangeNode | None]:
        if node is None:
            return None, None
        if node.key < key:
            node.right, right = cls._split(node.right, key)
            cls._refresh(node)
            return node, right
        left, node.left = cls._split(node.left, key)
        cls._refresh(node)
        return left, node

    @classmethod
    def _merge(
        cls,
        left: _TimestampRangeNode | None,
        right: _TimestampRangeNode | None,
    ) -> _TimestampRangeNode | None:
        if left is None:
            return right
        if right is None:
            return left
        if left.priority > right.priority:
            left.right = cls._merge(left.right, right)
            cls._refresh(left)
            return left
        right.left = cls._merge(left, right.left)
        cls._refresh(right)
        return right

    @classmethod
    def _insert(
        cls,
        root: _TimestampRangeNode | None,
        node: _TimestampRangeNode,
    ) -> _TimestampRangeNode:
        if root is None:
            return node
        if node.key == root.key:
            return root
        if node.priority > root.priority:
            node.left, node.right = cls._split(root, node.key)
            cls._refresh(node)
            return node
        if node.key < root.key:
            root.left = cls._insert(root.left, node)
        else:
            root.right = cls._insert(root.right, node)
        cls._refresh(root)
        return root

    @classmethod
    def _remove(
        cls,
        root: _TimestampRangeNode | None,
        key: tuple[float, int],
    ) -> _TimestampRangeNode | None:
        if root is None:
            return None
        if key == root.key:
            return cls._merge(root.left, root.right)
        if key < root.key:
            root.left = cls._remove(root.left, key)
        else:
            root.right = cls._remove(root.right, key)
        cls._refresh(root)
        return root

    def add(self, timestamp: float, index: int) -> None:
        self._root = self._insert(
            self._root,
            _TimestampRangeNode(
                key=(timestamp, index),
                priority=self._priority(index),
            ),
        )

    def remove(self, timestamp: float, index: int) -> None:
        self._root = self._remove(self._root, (timestamp, index))

    def latest(self) -> int | None:
        return self._root.max_index if self._root is not None else None

    def latest_in_range(self, lower: float, upper: float) -> int | None:
        left, middle_and_right = self._split(self._root, (lower, -1))
        middle, right = self._split(middle_and_right, (upper, math.inf))
        result = middle.max_index if middle is not None else None
        self._root = self._merge(left, self._merge(middle, right))
        return result


class _MessageMatchIndex:
    """Versioned indexes for finding the latest semantic duplicate.

    The legacy implementation scanned every previously emitted row backwards.
    Identity and result-surface matches use max-heaps. Ordinary matches use an
    exact timestamp range tree, so backfilling old history cannot repeatedly
    scan newer same-content rows outside the duplicate window. Replacing a
    merged row bumps its heap version and moves its timestamp-tree entry.
    """

    def __init__(
        self,
        owner: Any,
        messages: list[dict[str, Any]],
    ) -> None:
        self._owner = owner
        self._messages = messages
        self._versions: list[int] = [0 for _ in messages]
        self._states = [
            _MessageMatchState.from_message(owner, message)
            for message in messages
        ]
        self._identity_heaps: dict[tuple[str, str], list[tuple[int, int]]] = {}
        self._timed_trees: dict[tuple[str, str, str, str], _TimestampRangeTree] = {}
        self._timed_engine_trees: dict[tuple[str, str, str, str], _TimestampRangeTree] = {}
        self._timed_unbounded_heaps: dict[tuple[str, str, str, str], list[tuple[int, int]]] = {}
        self._timed_engine_unbounded_heaps: dict[tuple[str, str, str, str], list[tuple[int, int]]] = {}
        self._result_heaps: dict[tuple[str, str, str], list[tuple[int, int]]] = {}
        self._result_engine_heaps: dict[tuple[str, str, str], list[tuple[int, int]]] = {}
        for index in range(len(messages)):
            self._push(index)

    @staticmethod
    def _push_heap(
        heaps: dict[Any, list[tuple[int, int]]],
        key: Any,
        index: int,
        version: int,
    ) -> None:
        heapq.heappush(heaps.setdefault(key, []), (-index, version))

    @staticmethod
    def _semantic_key(state: _MessageMatchState) -> tuple[str, str, str]:
        return (state.channel_id, state.role_bucket, state.normalized_content)

    @classmethod
    def _timed_key(cls, state: _MessageMatchState) -> tuple[str, str, str, str]:
        return (*cls._semantic_key(state), state.reply_to_id)

    @staticmethod
    def _timestamp_is_unbounded(timestamp: float) -> bool:
        # The legacy predicate deliberately skipped its window check for zero;
        # NaN also made ``abs(delta) > window`` false and must stay equivalent.
        return timestamp == 0 or math.isnan(timestamp)

    @staticmethod
    def _float_to_ordered_int(value: float) -> int:
        """Map an IEEE-754 double to an integer with numeric sort order."""
        bits = struct.unpack(">Q", struct.pack(">d", value))[0]
        if bits & (1 << 63):
            return (~bits) & ((1 << 64) - 1)
        return bits | (1 << 63)

    @staticmethod
    def _ordered_int_to_float(value: int) -> float:
        if value & (1 << 63):
            bits = value & ((1 << 63) - 1)
        else:
            bits = (~value) & ((1 << 64) - 1)
        return struct.unpack(">d", struct.pack(">Q", bits))[0]

    @classmethod
    def _finite_timestamp_match_bounds(
        cls,
        candidate_timestamp: float,
        window: float,
    ) -> tuple[float, float]:
        """Exact finite-float bounds accepted by the legacy delta predicate.

        Computing ``candidate +/- window`` is subtly insufficient around a
        rounding boundary (for example ``2.0 - (-1e-300)`` rounds to exactly
        ``2.0``).  Binary searching the ordered double domain preserves the
        old IEEE-754 comparison exactly.  The domain is fixed at 64 bits, so
        this adds constant work before the tree's O(log n) range query.
        """
        candidate_order = cls._float_to_ordered_int(candidate_timestamp)

        lower_rejected = cls._float_to_ordered_int(-math.inf)
        lower_accepted = candidate_order
        while lower_rejected + 1 < lower_accepted:
            middle = (lower_rejected + lower_accepted) // 2
            value = cls._ordered_int_to_float(middle)
            if abs(value - candidate_timestamp) <= window:
                lower_accepted = middle
            else:
                lower_rejected = middle

        upper_accepted = candidate_order
        upper_rejected = cls._float_to_ordered_int(math.inf)
        while upper_accepted + 1 < upper_rejected:
            middle = (upper_accepted + upper_rejected) // 2
            value = cls._ordered_int_to_float(middle)
            if abs(value - candidate_timestamp) <= window:
                upper_accepted = middle
            else:
                upper_rejected = middle

        return (
            cls._ordered_int_to_float(lower_accepted),
            cls._ordered_int_to_float(upper_accepted),
        )

    @staticmethod
    def _add_to_tree(
        trees: dict[tuple[str, str, str, str], _TimestampRangeTree],
        key: tuple[str, str, str, str],
        timestamp: float,
        index: int,
    ) -> None:
        trees.setdefault(key, _TimestampRangeTree()).add(timestamp, index)

    def _push(self, index: int) -> None:
        state = self._states[index]
        version = self._versions[index]
        for identity_key in state.identity_keys:
            self._push_heap(
                self._identity_heaps,
                (state.channel_id, identity_key),
                index,
                version,
            )

        semantic_key = self._semantic_key(state)
        timed_key = self._timed_key(state)
        if self._timestamp_is_unbounded(state.timestamp):
            self._push_heap(
                self._timed_unbounded_heaps,
                timed_key,
                index,
                version,
            )
            if state.has_engine_source:
                self._push_heap(
                    self._timed_engine_unbounded_heaps,
                    timed_key,
                    index,
                    version,
                )
        else:
            self._add_to_tree(
                self._timed_trees,
                timed_key,
                state.timestamp,
                index,
            )
            if state.has_engine_source:
                self._add_to_tree(
                    self._timed_engine_trees,
                    timed_key,
                    state.timestamp,
                    index,
                )
        if state.is_result_surface:
            self._push_heap(self._result_heaps, semantic_key, index, version)
            if state.has_engine_source:
                self._push_heap(self._result_engine_heaps, semantic_key, index, version)

    def prepare(self, message: dict[str, Any]) -> _MessageMatchState:
        return _MessageMatchState.from_message(self._owner, message)

    def append(
        self,
        message: dict[str, Any],
        *,
        prepared_state: _MessageMatchState | None = None,
    ) -> int:
        index = len(self._messages)
        self._messages.append(message)
        self._versions.append(0)
        self._states.append(prepared_state or self.prepare(message))
        self._push(index)
        return index

    def replace(self, index: int, message: dict[str, Any]) -> None:
        old_state = self._states[index]
        if not self._timestamp_is_unbounded(old_state.timestamp):
            timed_key = self._timed_key(old_state)
            tree = self._timed_trees.get(timed_key)
            if tree is not None:
                tree.remove(old_state.timestamp, index)
            if old_state.has_engine_source:
                engine_tree = self._timed_engine_trees.get(timed_key)
                if engine_tree is not None:
                    engine_tree.remove(old_state.timestamp, index)
        self._messages[index] = message
        self._versions[index] += 1
        self._states[index] = _MessageMatchState.from_message(self._owner, message)
        self._push(index)

    def _latest_from_heap(
        self,
        heap: list[tuple[int, int]] | None,
        candidate: _MessageMatchState,
        excluded_message_ids: set[str],
    ) -> int | None:
        if not heap:
            return None
        while heap:
            negative_index, version = heap[0]
            index = -negative_index
            if version != self._versions[index]:
                heapq.heappop(heap)
                continue
            message_id = str(self._messages[index].get("message_id", "") or "")
            if message_id in excluded_message_ids:
                heapq.heappop(heap)
                continue
            existing = self._states[index]
            if existing.matches(
                candidate,
                duplicate_window=self._owner._DUPLICATE_WINDOW_SECONDS,
            ):
                return index
            # Identity/result/unbounded timed buckets are exact. A current
            # non-match therefore cannot become valid for this bucket later.
            heapq.heappop(heap)
        return None

    def _latest_from_time_tree(
        self,
        tree: _TimestampRangeTree | None,
        candidate: _MessageMatchState,
        excluded_message_ids: set[str],
    ) -> int | None:
        if tree is None:
            return None
        while True:
            if self._timestamp_is_unbounded(candidate.timestamp):
                index = tree.latest()
            else:
                window = self._owner._DUPLICATE_WINDOW_SECONDS
                if math.isfinite(candidate.timestamp):
                    lower, upper = self._finite_timestamp_match_bounds(
                        candidate.timestamp,
                        window,
                    )
                else:
                    lower = upper = candidate.timestamp
                index = tree.latest_in_range(lower, upper)
            if index is None:
                return None
            existing = self._states[index]
            message_id = str(self._messages[index].get("message_id", "") or "")
            if message_id not in excluded_message_ids and existing.matches(
                candidate,
                duplicate_window=self._owner._DUPLICATE_WINDOW_SECONDS,
            ):
                return index
            # Consumed entries never become eligible again during this index's
            # lifetime. Signature changes use ``replace`` and reinsert exactly.
            tree.remove(existing.timestamp, index)

    def latest_match(
        self,
        candidate_message: dict[str, Any],
        *,
        excluded_message_ids: set[str] | None = None,
        prepared_state: _MessageMatchState | None = None,
    ) -> int | None:
        candidate = prepared_state or self.prepare(candidate_message)
        excluded = excluded_message_ids or set()
        heaps: list[list[tuple[int, int]] | None] = []
        for identity_key in candidate.identity_keys:
            heaps.append(self._identity_heaps.get((candidate.channel_id, identity_key)))

        semantic_key = self._semantic_key(candidate)
        timed_key = self._timed_key(candidate)
        if candidate.has_engine_source:
            timed_tree = self._timed_trees.get(timed_key)
            heaps.append(self._timed_unbounded_heaps.get(timed_key))
        else:
            timed_tree = self._timed_engine_trees.get(timed_key)
            heaps.append(self._timed_engine_unbounded_heaps.get(timed_key))
        if candidate.is_result_surface:
            if candidate.has_engine_source:
                heaps.append(self._result_heaps.get(semantic_key))
            else:
                heaps.append(self._result_engine_heaps.get(semantic_key))

        matches: list[int] = [
            index
            for heap in heaps
            if (index := self._latest_from_heap(heap, candidate, excluded)) is not None
        ]
        timed_index = self._latest_from_time_tree(timed_tree, candidate, excluded)
        if timed_index is not None:
            matches.append(timed_index)
        return max(matches) if matches else None


class ChatStore:
    """Chat channels + messages in ui_state.db.

    Channel types (matching frontend ChatStore.ts):
      "session"    → id: "session:{task_id}", per-task conversation
      "activity"   → id: "activity", global activity feed
      "secretary"  → id: "secretary", policy/rules channel
    """

    _DUPLICATE_WINDOW_SECONDS = 2.0
    _RESULT_SURFACE_PRIORITY = {
        "child_task_result": 80,
        "child_task_result_retry": 79,
        "company_role_result": 75,
        "company_role_result_retry": 74,
        "child_result": 70,
        "runtime_v2_assistant": 60,
        "runtime_v2_company_assistant": 20,
        "top_level_reply": 40,
        "worker_notification": 10,
    }

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    @staticmethod
    def _normalize_message_content(content: Any) -> str:
        return CodexAdapter.normalize_transcript_text(str(content or ""))

    @classmethod
    def _normalize_duplicate_content(cls, content: Any) -> str:
        normalized = cls._normalize_message_content(content)
        normalized = "\n".join(line.rstrip() for line in normalized.splitlines()).strip()
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        normalized = cls._strip_narrative_title_prefix(normalized)
        paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
        if len(paragraphs) > 1 and re.match(r"^Verification:\s", paragraphs[-1], flags=re.IGNORECASE):
            normalized = "\n\n".join(paragraphs[:-1]).strip()
        return normalized

    @staticmethod
    def _strip_narrative_title_prefix(content: str) -> str:
        trimmed = str(content or "").strip()
        # Only discard an explicit, anchored narrative wrapper.  The former
        # fallback used the first ``": "`` anywhere in the first 160
        # characters, so ordinary Markdown such as ``**Work Item 1: ...**``
        # was progressively truncated every time a sync was merged.  Peeling
        # explicit nested wrappers to a fixed point keeps this comparison
        # helper idempotent without interpreting body punctuation as structure.
        while True:
            markdown_title = re.match(r"^\*\*(.{8,160}?)\*\*:\s+([\s\S]+)$", trimmed)
            if not markdown_title:
                return trimmed
            body = markdown_title.group(2).strip()
            if len(body) < 80 or body == trimmed:
                return trimmed
            trimmed = body

    @classmethod
    def _select_duplicate_display_content(
        cls,
        preferred: dict[str, Any],
        secondary: dict[str, Any],
    ) -> str:
        """Choose an exact input body; never persist the comparison key itself."""
        preferred_content = str(preferred.get("content", "") or "")
        secondary_content = str(secondary.get("content", "") or "")
        preferred_key = cls._normalize_duplicate_content(preferred_content)
        if not preferred_key or preferred_key != cls._normalize_duplicate_content(secondary_content):
            return preferred_content
        # If one real surface is already the comparison body while the other
        # carries an explicit narrative wrapper/footer, reuse that real body.
        # This preserves the historical visible answer without manufacturing
        # display text from a lossy canonicalization function.
        if (
            secondary_content.strip() == preferred_key
            and preferred_content.strip() != preferred_key
        ):
            return secondary_content
        return preferred_content

    @staticmethod
    def _message_timestamp(message: dict[str, Any]) -> float:
        value = message.get("created_at")
        if value is None:
            value = message.get("timestamp")
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _message_role_bucket(message: dict[str, Any]) -> str:
        sender = str(message.get("sender", "") or "").strip().lower()
        metadata = dict(message.get("metadata", {}) or {})
        role = str(metadata.get("role", "") or "").strip().lower()
        if sender == "user" or role == "user":
            return "user"
        return "assistant"

    @classmethod
    def _message_preference_score(cls, message: dict[str, Any]) -> int:
        metadata = dict(message.get("metadata", {}) or {})
        sender = str(message.get("sender", "") or "").strip().lower()
        score = 0
        result_priority = cls._message_result_surface_priority(message)
        if result_priority:
            score += 1000 + result_priority
        if metadata.get("source") == "engine":
            score += 100
        if sender and sender != "system":
            score += 20
        if sender not in ("", "assistant", "system", "user"):
            score += 5
        if message.get("reply_to_id"):
            score += 2
        score += min(len(metadata), 10)
        return score

    @classmethod
    def _message_has_engine_source(cls, message: dict[str, Any]) -> bool:
        metadata = dict(message.get("metadata", {}) or {})
        return str(metadata.get("source", "") or "").strip().lower() == "engine"

    @classmethod
    def _message_result_surface_priority(cls, message: dict[str, Any]) -> int:
        metadata = dict(message.get("metadata", {}) or {})
        transcript_kind = str(metadata.get("transcript_kind", "") or "").strip()
        if transcript_kind:
            return cls._RESULT_SURFACE_PRIORITY.get(transcript_kind, 0)
        kind = str(metadata.get("kind", "") or "").strip()
        if kind == "worker_notification":
            return cls._RESULT_SURFACE_PRIORITY["worker_notification"]
        return cls._RESULT_SURFACE_PRIORITY.get(kind, 0)

    @classmethod
    def _message_is_result_surface(cls, message: dict[str, Any]) -> bool:
        return cls._message_result_surface_priority(message) > 0

    @classmethod
    def _message_identity_keys(cls, message: dict[str, Any]) -> set[str]:
        metadata = dict(message.get("metadata", {}) or {})
        keys: set[str] = set()
        for value in (
            message.get("message_id"),
            message.get("id"),
            metadata.get("ui_message_id"),
        ):
            normalized = str(value or "").strip()
            if normalized:
                keys.add(normalized)
        result_delivery_id = str(metadata.get("result_delivery_id", "") or "").strip()
        if result_delivery_id:
            keys.add(f"result_delivery:{result_delivery_id}")
        return keys

    @classmethod
    def _messages_semantically_match(
        cls,
        existing: dict[str, Any],
        candidate: dict[str, Any],
    ) -> bool:
        return _MessageMatchState.from_message(cls, existing).matches(
            _MessageMatchState.from_message(cls, candidate),
            duplicate_window=cls._DUPLICATE_WINDOW_SECONDS,
        )

    @classmethod
    def _merge_duplicate_messages(
        cls,
        existing: dict[str, Any],
        candidate: dict[str, Any],
        *,
        prefer_candidate: bool = False,
    ) -> dict[str, Any]:
        preferred = existing
        secondary = candidate
        if (
            prefer_candidate
            or cls._message_preference_score(candidate) > cls._message_preference_score(existing)
        ):
            preferred = candidate
            secondary = existing

        merged = dict(secondary)
        merged.update(preferred)

        secondary_meta = dict(secondary.get("metadata", {}) or {})
        preferred_meta = dict(preferred.get("metadata", {}) or {})
        merged["metadata"] = {**secondary_meta, **preferred_meta}
        merged["content"] = cls._select_duplicate_display_content(preferred, secondary)

        shared_ids = cls._message_identity_keys(existing) & cls._message_identity_keys(candidate)
        canonical_id = ""
        if shared_ids:
            for value in (
                existing.get("message_id"),
                existing.get("id"),
                candidate.get("message_id"),
                candidate.get("id"),
                preferred_meta.get("ui_message_id"),
                secondary_meta.get("ui_message_id"),
            ):
                normalized = str(value or "").strip()
                if normalized and normalized in shared_ids:
                    canonical_id = normalized
                    break
        if canonical_id:
            merged["message_id"] = canonical_id

        mentions: list[str] = []
        for values in (secondary.get("mentions", []), preferred.get("mentions", [])):
            for value in values or []:
                if value not in mentions:
                    mentions.append(value)
        merged["mentions"] = mentions

        merged_ts = cls._message_timestamp(preferred) or cls._message_timestamp(secondary)
        if merged_ts:
            merged["created_at"] = merged_ts
            if "timestamp" in preferred or "timestamp" in secondary:
                merged["timestamp"] = merged_ts
        return merged

    @classmethod
    def _message_persisted_equal(
        cls,
        existing: dict[str, Any],
        candidate: dict[str, Any],
    ) -> bool:
        return (
            str(existing.get("sender", "") or "") == str(candidate.get("sender", "") or "")
            and str(existing.get("sender_name", "") or "") == str(candidate.get("sender_name", "") or "")
            # Display content is persisted data, not a duplicate-comparison
            # key.  Exact comparison lets authoritative transcript backfill
            # repair a legacy row whose body was destructively normalized.
            and str(existing.get("content", "") or "") == str(candidate.get("content", "") or "")
            and cls._message_timestamp(existing) == cls._message_timestamp(candidate)
            and str(existing.get("reply_to_id", "") or "") == str(candidate.get("reply_to_id", "") or "")
            and list(existing.get("mentions", []) or []) == list(candidate.get("mentions", []) or [])
            and dict(existing.get("metadata", {}) or {}) == dict(candidate.get("metadata", {}) or {})
        )

    def _dedupe_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        match_index = _MessageMatchIndex(
            self,
            deduped,
        )
        for message in sorted(messages, key=self._message_timestamp):
            prepared_state = match_index.prepare(message)
            duplicate_index = match_index.latest_match(
                message,
                prepared_state=prepared_state,
            )
            if duplicate_index is None:
                match_index.append(message, prepared_state=prepared_state)
                continue
            match_index.replace(
                duplicate_index,
                self._merge_duplicate_messages(deduped[duplicate_index], message),
            )
        return deduped

    async def _message_scope(self, message_id: str) -> tuple[str, str] | None:
        cursor = await self._db.execute(
            "SELECT channel_id, project_id FROM messages WHERE message_id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return str(row[0] or ""), str(row[1] or "default")

    async def message_scope(self, message_id: str) -> tuple[str, str] | None:
        """(channel_id, project_id) of a persisted message, or None if absent.

        Used for idempotent client sends: a re-delivered ``session_send`` carries
        the same client-generated ``ui_message_id``, so an existing row in the
        same scope identifies the duplicate.
        """
        if not str(message_id or "").strip():
            return None
        return await self._message_scope(str(message_id).strip())

    async def _persist_merged_message(
        self,
        existing: dict[str, Any],
        merged: dict[str, Any],
        *,
        channel_id: str,
        project_id: str,
        preserve_timestamp: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        """Persist a duplicate merge while retaining the mounted cache row.

        Semantic transcript duplicates can have a different source message id.
        Keeping the existing cache identity (and, for semantic replacement, its
        timestamp) upgrades the row in place instead of moving it in the UI.
        """
        persisted_id = str(existing.get("message_id", "") or existing.get("id", "") or "")
        existing_timestamp = self._message_timestamp(existing)
        merged_timestamp = (
            existing_timestamp
            if preserve_timestamp and existing_timestamp
            else self._message_timestamp(merged) or time.time()
        )
        persisted = {
            **merged,
            "message_id": persisted_id,
            "channel_id": channel_id,
            "timestamp": merged_timestamp,
            "created_at": merged_timestamp,
        }
        if self._message_persisted_equal(existing, persisted):
            return persisted, False
        await self._db.execute(
            "UPDATE messages SET sender = ?, sender_name = ?, content = ?, timestamp = ?, "
            "reply_to_id = ?, mentions = ?, metadata = ? WHERE message_id = ? AND channel_id = ? AND project_id = ?",
            (
                persisted["sender"],
                persisted["sender_name"],
                persisted["content"],
                merged_timestamp,
                persisted.get("reply_to_id"),
                json.dumps(persisted.get("mentions", [])),
                json.dumps(persisted.get("metadata", {})),
                persisted_id,
                channel_id,
                project_id,
            ),
        )
        return persisted, True

    async def _merge_into_same_scope_row(
        self,
        message_id: str,
        *,
        channel_id: str,
        project_id: str,
        candidate: dict[str, Any],
        prefer_candidate: bool = False,
    ) -> tuple[dict[str, Any], bool] | None:
        """Merge ``candidate`` into an already-persisted row with the same id/scope.

        Returns ``(merged row, materially changed)`` or ``None`` when the row
        could not be loaded. Backfill and the live insert path can race on the
        same message id; the duplicate must merge in place, never be re-inserted
        under a scoped alias id in the same channel.
        """
        cursor = await self._db.execute(
            "SELECT message_id, channel_id, sender, sender_name, content, "
            "timestamp, reply_to_id, mentions, metadata "
            "FROM messages WHERE message_id = ? AND channel_id = ? AND project_id = ?",
            (message_id, channel_id, project_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        existing = self._row_to_message_dict(row)
        merged = self._merge_duplicate_messages(
            existing,
            candidate,
            prefer_candidate=prefer_candidate,
        )
        return await self._persist_merged_message(
            existing,
            merged,
            channel_id=channel_id,
            project_id=project_id,
        )

    async def _allocate_scoped_message_id(
        self,
        message_id: str,
        *,
        channel_id: str,
        project_id: str,
    ) -> str:
        base = f"{message_id}::{project_id}::{channel_id}"
        candidate = base
        suffix = 1
        while await self._message_scope(candidate):
            suffix += 1
            candidate = f"{base}::{suffix}"
        return candidate

    def _row_to_message_dict(self, row: Any) -> dict[str, Any]:
        metadata = json.loads(row[8]) if row[8] else {}
        sender_name = str(row[3] or "")
        transcript_kind = str(metadata.get("transcript_kind", "") or metadata.get("kind", "") or "").strip()
        if (
            sender_name.strip().lower().replace(" ", "_") == "task_generalist"
            and transcript_kind in {
                "",
                "runtime_v2_assistant",
                "runtime_v2_company_assistant",
                "runtime_v2_intermediate_assistant",
                "top_level_reply",
            }
        ):
            sender_name = "AutoSwarm"
        return {
            "message_id": row[0],
            "channel_id": row[1],
            "sender": row[2],
            "sender_name": sender_name,
            "content": self._normalize_message_content(row[4]),
            "created_at": row[5],
            "reply_to_id": row[6],
            "mentions": json.loads(row[7]) if row[7] else [],
            "metadata": metadata,
        }

    async def _retry_locked(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """Run a write operation, retrying briefly on transient sqlite lock errors.

        Another process sharing ui_state.db (a second server, the CLI) can hold
        the write lock past busy_timeout; a short backoff usually clears it.
        """
        last_error: BaseException | None = None
        for attempt in range(_WRITE_RETRY_ATTEMPTS):
            try:
                return await operation()
            except sqlite3.OperationalError as exc:
                if not _is_locked_error(exc):
                    raise
                last_error = exc
                try:
                    await self._db.rollback()
                except Exception:
                    pass
                await asyncio.sleep(_WRITE_RETRY_BASE_DELAY_SECONDS * (2 ** attempt))
        assert last_error is not None
        raise last_error

    async def initialize(self) -> None:
        """Create tables if not exist."""
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                office_id TEXT,
                participants TEXT DEFAULT '[]',
                created_at REAL NOT NULL
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL,
                reply_to_id TEXT,
                mentions TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS task_progress (
                task_id TEXT PRIMARY KEY,
                entries TEXT DEFAULT '[]',
                updated_at REAL NOT NULL
            )
        """)
        await self._db.commit()

        # Migration: add project_id column if missing
        for tbl in ("channels", "messages", "task_progress"):
            try:
                await self._db.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN project_id TEXT DEFAULT 'default'"
                )
            except Exception:
                pass  # column already exists
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_channels_project ON channels(project_id)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_project ON messages(project_id)"
        )
        await self._ensure_project_scoped_primary_keys()
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_channels_project ON channels(project_id)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_project ON messages(project_id)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_channels_project_type_created "
            "ON channels(project_id, type, created_at DESC)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_project_timestamp "
            "ON messages(project_id, timestamp DESC)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_project_channel_timestamp "
            "ON messages(project_id, channel_id, timestamp DESC)"
        )
        await self._db.commit()

    async def _ensure_project_scoped_primary_keys(self) -> None:
        """Migrate legacy single-column UI state keys to project-scoped keys."""

        async def _pk_columns(table: str) -> list[str]:
            cursor = await self._db.execute(f"PRAGMA table_info({table})")
            rows = await cursor.fetchall()
            pk_rows = sorted(
                ((int(row[5] or 0), str(row[1] or "")) for row in rows if int(row[5] or 0) > 0),
                key=lambda item: item[0],
            )
            return [name for _, name in pk_rows]

        async def _migrate(table: str, create_sql: str, columns: list[str], expected_pk: list[str]) -> None:
            if await _pk_columns(table) == expected_pk:
                return
            staging = f"{table}__project_scope"
            await self._db.execute(f"DROP TABLE IF EXISTS {staging}")
            await self._db.execute(create_sql.format(table=staging))
            column_list = ", ".join(columns)
            await self._db.execute(
                f"INSERT OR IGNORE INTO {staging} ({column_list}) "
                f"SELECT {column_list} FROM {table}"
            )
            await self._db.execute(f"DROP TABLE {table}")
            await self._db.execute(f"ALTER TABLE {staging} RENAME TO {table}")

        await _migrate(
            "channels",
            """
            CREATE TABLE {table} (
                channel_id TEXT NOT NULL,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                office_id TEXT,
                participants TEXT DEFAULT '[]',
                created_at REAL NOT NULL,
                project_id TEXT DEFAULT 'default',
                PRIMARY KEY (channel_id, project_id)
            )
            """,
            ["channel_id", "type", "name", "office_id", "participants", "created_at", "project_id"],
            ["channel_id", "project_id"],
        )
        await _migrate(
            "task_progress",
            """
            CREATE TABLE {table} (
                task_id TEXT NOT NULL,
                entries TEXT DEFAULT '[]',
                updated_at REAL NOT NULL,
                project_id TEXT DEFAULT 'default',
                PRIMARY KEY (task_id, project_id)
            )
            """,
            ["task_id", "entries", "updated_at", "project_id"],
            ["task_id", "project_id"],
        )

    async def _ensure_channel(
        self,
        channel_id: str,
        channel_type: str,
        name: str,
        participants: list[str],
        office_id: str | None = None,
        project_id: str = "default",
    ) -> None:
        """Create channel if it doesn't exist."""
        cursor = await self._db.execute(
            "SELECT channel_id FROM channels WHERE channel_id = ? AND project_id = ?",
            (channel_id, project_id),
        )
        if await cursor.fetchone():
            # Update participants if channel exists
            await self._db.execute(
                "UPDATE channels SET participants = ? WHERE channel_id = ? AND project_id = ?",
                (json.dumps(participants), channel_id, project_id),
            )
        else:
            await self._db.execute(
                "INSERT INTO channels (channel_id, type, name, office_id, participants, created_at, project_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (channel_id, channel_type, name, office_id, json.dumps(participants), time.time(), project_id),
            )
        await self._db.commit()

    async def create_channel(
        self,
        channel_type: str,
        name: str,
        participants: list[str] | None = None,
        office_id: str | None = None,
        channel_id: str | None = None,
        project_id: str = "default",
    ) -> dict[str, Any]:
        """Create a new chat channel. Returns channel dict in backend format."""
        cid = channel_id or str(uuid.uuid4())
        now = time.time()
        parts = participants or []
        cursor = await self._db.execute(
            "SELECT type, name, office_id, participants, created_at FROM channels "
            "WHERE channel_id = ? AND project_id = ?",
            (cid, project_id),
        )
        existing = await cursor.fetchone()
        created_at = float(existing[4]) if existing and existing[4] is not None else now
        channel = {
            "channel_id": cid,
            "type": channel_type,
            "name": name,
            "office_id": office_id,
            "participants": parts,
            "created_at": created_at,
            "project_id": project_id,
        }
        if existing is not None:
            # Callers (e.g. session_detail polling) invoke this on every
            # request; skip the write when nothing changed so a read-only
            # view does not generate a constant write load on ui_state.db.
            try:
                existing_parts = json.loads(existing[3]) if existing[3] else []
            except (json.JSONDecodeError, TypeError):
                existing_parts = None
            unchanged = (
                str(existing[0] or "") == channel_type
                and str(existing[1] or "") == name
                and (existing[2] or None) == (office_id or None)
                and existing_parts == parts
            )
            if unchanged:
                return channel

        async def _write() -> None:
            await self._db.execute(
                "INSERT OR REPLACE INTO channels (channel_id, type, name, office_id, participants, created_at, project_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, channel_type, name, office_id, json.dumps(parts), created_at, project_id),
            )
            await self._db.commit()

        await self._retry_locked(_write)
        return channel

    async def insert_message(
        self,
        channel_id: str,
        sender: str,
        sender_name: str,
        content: str,
        reply_to_id: str | None = None,
        mentions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
        project_id: str = "default",
        created_at: float | None = None,
    ) -> dict[str, Any]:
        """Insert a message. Returns message dict in backend format (snake_case)."""
        mid = message_id or str(uuid.uuid4())
        now = float(created_at) if created_at is not None else time.time()

        async def _write() -> None:
            await self._db.execute(
                "INSERT OR REPLACE INTO messages "
                "(message_id, channel_id, sender, sender_name, content, timestamp, "
                "reply_to_id, mentions, metadata, project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mid, channel_id, sender, sender_name, content, now,
                    reply_to_id,
                    json.dumps(mentions or []),
                    json.dumps(metadata or {}),
                    project_id,
                ),
            )
            await self._db.commit()

        await self._retry_locked(_write)
        return {
            "message_id": mid,
            "channel_id": channel_id,
            "sender": sender,
            "sender_name": sender_name,
            "content": content,
            "created_at": now,
            "reply_to_id": reply_to_id,
            "mentions": mentions or [],
            "metadata": metadata or {},
            "project_id": project_id,
        }

    async def get_channels(self, project_id: str = "default") -> list[dict[str, Any]]:
        """Return channels for a project in backend format (snake_case)."""
        cursor = await self._db.execute(
            "SELECT channel_id, type, name, office_id, participants, created_at "
            "FROM channels WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "channel_id": r[0],
                "type": r[1],
                "name": r[2],
                "office_id": r[3],
                "participants": json.loads(r[4]) if r[4] else [],
                "created_at": r[5],
            }
            for r in rows
        ]

    async def get_messages(self, project_id: str = "default", limit: int = 500) -> list[dict[str, Any]]:
        """Return recent messages for a project in backend format (snake_case)."""
        fetch_limit = max(limit * 4, limit, 1)
        cursor = await self._db.execute(
            "SELECT message_id, channel_id, sender, sender_name, content, "
            "timestamp, reply_to_id, mentions, metadata "
            "FROM messages WHERE project_id = ? ORDER BY timestamp DESC LIMIT ?",
            (project_id, fetch_limit),
        )
        rows = await cursor.fetchall()
        messages = [self._row_to_message_dict(row) for row in rows]
        # Return in chronological order
        messages.reverse()
        messages = self._dedupe_messages(messages)
        if len(messages) > limit:
            messages = messages[-limit:]
        return messages

    async def prune_stale_channels(self, valid_agent_ids: set[str], project_id: str = "default") -> int:
        """Remove DM and office channels that reference only stale agents.

        Returns the number of channels deleted.
        """
        channels = await self.get_channels(project_id)
        pruned = 0
        for ch in channels:
            if ch["type"] in ("global", "activity", "session", "secretary"):
                continue  # Never prune global, activity, session, or secretary channels
            participants = ch.get("participants", [])
            agent_participants = [p for p in participants if p != "user"]
            if not agent_participants:
                continue
            # If none of the agent participants exist in valid set, prune
            if not any(aid in valid_agent_ids for aid in agent_participants):
                await self._db.execute(
                    "DELETE FROM messages WHERE channel_id = ? AND project_id = ?",
                    (ch["channel_id"], project_id),
                )
                await self._db.execute(
                    "DELETE FROM channels WHERE channel_id = ? AND project_id = ?",
                    (ch["channel_id"], project_id),
                )
                pruned += 1
        if pruned:
            await self._db.commit()
        return pruned

    # ── Session channel methods ──────────────────────────────────────────

    async def create_session_channel(
        self,
        task_id: str,
        title: str,
        participants: list[str] | None = None,
        project_id: str = "default",
    ) -> dict[str, Any]:
        """Create a session channel tied to a task. Channel id = session:{task_id}."""
        channel_id = f"session:{task_id}"
        parts = participants or ["user"]
        return await self.create_channel(
            channel_type="session",
            name=title,
            participants=parts,
            channel_id=channel_id,
            project_id=project_id,
        )

    async def ensure_activity_channel(self, project_id: str = "default") -> None:
        """Ensure the activity monitoring channel exists for this project and clean up legacy channels."""
        await self._ensure_channel(
            channel_id=f"activity:{project_id}",
            channel_type="activity",
            name="Activity",
            participants=["user"],
            project_id=project_id,
        )
        # Remove legacy channel types (global, office, dm, cross-office) — only for this project
        await self._db.execute(
            "DELETE FROM channels WHERE project_id = ? AND type NOT IN ('session', 'activity', 'secretary')",
            (project_id,),
        )
        await self._db.commit()

    async def update_channel_name(self, channel_id: str, name: str, project_id: str = "default") -> None:
        """Update a channel's display name (e.g. auto-title from first message)."""
        await self._db.execute(
            "UPDATE channels SET name = ? WHERE channel_id = ? AND project_id = ?",
            (name, channel_id, project_id),
        )
        await self._db.commit()

    async def get_session_channels(self, project_id: str = "default") -> list[dict[str, Any]]:
        """Return session channels ordered by last activity (most recent first)."""
        cursor = await self._db.execute(
            "SELECT channel_id, type, name, office_id, participants, created_at "
            "FROM channels WHERE type = 'session' AND project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "channel_id": r[0],
                "type": r[1],
                "name": r[2],
                "office_id": r[3],
                "participants": json.loads(r[4]) if r[4] else [],
                "created_at": r[5],
            }
            for r in rows
        ]

    async def get_channel_message_count(self, channel_id: str, project_id: str = "default") -> int:
        """Return message count for a channel."""
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE channel_id = ? AND project_id = ?",
            (channel_id, project_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_channel_latest_timestamp(self, channel_id: str, project_id: str = "default") -> float | None:
        """Return the latest message timestamp for a channel (epoch seconds)."""
        cursor = await self._db.execute(
            "SELECT MAX(timestamp) FROM messages WHERE channel_id = ? AND project_id = ?",
            (channel_id, project_id),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None

    async def get_channel_stats(
        self,
        channel_ids: list[str],
        *,
        project_id: str | None = None,
    ) -> dict[str, dict[str, float | int | None]]:
        """Return message_count/latest_timestamp for many channels in one query."""
        normalized_ids = [str(channel_id or "").strip() for channel_id in channel_ids if str(channel_id or "").strip()]
        if not normalized_ids:
            return {}

        placeholders = ",".join("?" for _ in normalized_ids)
        params: list[Any] = list(normalized_ids)
        query = (
            "SELECT channel_id, COUNT(*), MAX(timestamp) "
            f"FROM messages WHERE channel_id IN ({placeholders})"
        )
        if project_id is not None:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " GROUP BY channel_id"

        stats: dict[str, dict[str, float | int | None]] = {
            channel_id: {"message_count": 0, "latest_timestamp": None}
            for channel_id in normalized_ids
        }
        cursor = await self._db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        for channel_id, message_count, latest_timestamp in rows:
            stats[str(channel_id)] = {
                "message_count": int(message_count or 0),
                "latest_timestamp": float(latest_timestamp) if latest_timestamp else None,
            }
        return stats

    async def get_channel_index_stats(
        self,
        channel_ids: list[str],
        *,
        project_id: str = "default",
        preview_chars: int = 180,
    ) -> dict[str, dict[str, Any]]:
        """Return count/latest timestamp/preview for many channels.

        This is intentionally smaller than ``get_messages(project_id)``: the
        project index only needs a row preview, not the whole project message
        cache.
        """
        normalized_ids = [
            str(channel_id or "").strip()
            for channel_id in channel_ids
            if str(channel_id or "").strip()
        ]
        if not normalized_ids:
            return {}

        placeholders = ",".join("?" for _ in normalized_ids)
        stats: dict[str, dict[str, Any]] = {
            channel_id: {
                "message_count": 0,
                "latest_timestamp": None,
                "latest_preview": "",
                "latest_sender": "",
                "latest_message_id": "",
            }
            for channel_id in normalized_ids
        }

        count_cursor = await self._db.execute(
            "SELECT channel_id, COUNT(*), MAX(timestamp) "
            f"FROM messages WHERE project_id = ? AND channel_id IN ({placeholders}) "
            "GROUP BY channel_id",
            tuple([project_id, *normalized_ids]),
        )
        for channel_id, message_count, latest_timestamp in await count_cursor.fetchall():
            bucket = stats.get(str(channel_id))
            if bucket is None:
                continue
            bucket["message_count"] = int(message_count or 0)
            bucket["latest_timestamp"] = float(latest_timestamp) if latest_timestamp else None

        latest_cursor = await self._db.execute(
            "SELECT channel_id, message_id, sender, sender_name, content, timestamp FROM ("
            "  SELECT channel_id, message_id, sender, sender_name, content, timestamp, "
            "         ROW_NUMBER() OVER (PARTITION BY channel_id ORDER BY timestamp DESC, message_id DESC) AS rn "
            f"  FROM messages WHERE project_id = ? AND channel_id IN ({placeholders})"
            ") WHERE rn = 1",
            tuple([project_id, *normalized_ids]),
        )
        max_preview = max(0, int(preview_chars or 0))
        for channel_id, message_id, sender, sender_name, content, timestamp in await latest_cursor.fetchall():
            bucket = stats.get(str(channel_id))
            if bucket is None:
                continue
            preview = " ".join(str(content or "").split())
            if max_preview and len(preview) > max_preview:
                preview = (
                    preview[:max_preview]
                    if max_preview < 4
                    else preview[: max_preview - 3].rstrip() + "..."
                )
            bucket.update({
                "latest_timestamp": float(timestamp) if timestamp else bucket.get("latest_timestamp"),
                "latest_preview": preview,
                "latest_sender": str(sender_name or sender or ""),
                "latest_message_id": str(message_id or ""),
            })
        return stats

    async def ensure_secretary_channel(self, project_id: str = "default") -> dict[str, Any]:
        """Ensure the secretary channel exists for this project. Returns channel dict."""
        channel_id = f"secretary:{project_id}"
        cursor = await self._db.execute(
            "SELECT channel_id FROM channels WHERE channel_id = ? AND project_id = ?",
            (channel_id, project_id),
        )
        if await cursor.fetchone():
            return {
                "channel_id": channel_id,
                "type": "secretary",
                "name": "Secretary",
                "office_id": None,
                "participants": ["user"],
                "created_at": 0,
            }
        return await self.create_channel(
            channel_type="secretary",
            name="Secretary",
            participants=["user"],
            channel_id=channel_id,
            project_id=project_id,
        )

    async def delete_channel(self, channel_id: str, project_id: str = "default") -> None:
        """Delete a single channel and all its messages."""
        await self._db.execute(
            "DELETE FROM messages WHERE channel_id = ? AND project_id = ?",
            (channel_id, project_id),
        )
        await self._db.execute(
            "DELETE FROM channels WHERE channel_id = ? AND project_id = ?",
            (channel_id, project_id),
        )
        await self._db.commit()

    async def delete_activity_messages_for_task(self, project_id: str, task_id: str) -> int:
        """Delete messages from the activity channel that belong to a specific task."""
        channel_id = f"activity:{project_id}"
        cursor = await self._db.execute(
            "DELETE FROM messages WHERE channel_id = ? AND project_id = ? AND json_extract(metadata, '$.task_id') = ?",
            (channel_id, project_id, task_id),
        )
        await self._db.commit()
        return cursor.rowcount

    async def delete_project_data(self, project_id: str) -> int:
        """Delete ALL channels, messages, and progress for a project. Returns count of deleted channels."""
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM channels WHERE project_id = ?", (project_id,)
        )
        row = await cursor.fetchone()
        count = row[0] if row else 0
        await self._db.execute(
            "DELETE FROM messages WHERE project_id = ?", (project_id,)
        )
        await self._db.execute(
            "DELETE FROM channels WHERE project_id = ?", (project_id,)
        )
        await self._db.execute(
            "DELETE FROM task_progress WHERE project_id = ?", (project_id,)
        )
        await self._db.commit()
        return count

    async def project_data_exists(self, project_id: str) -> bool:
        """Return whether UI chat/progress rows exist for a project."""
        for table in ("channels", "messages", "task_progress"):
            cursor = await self._db.execute(
                f"SELECT 1 FROM {table} WHERE project_id = ? LIMIT 1",
                (project_id,),
            )
            if await cursor.fetchone():
                return True
        return False

    async def rename_project_data(self, old_project_id: str, new_project_id: str) -> dict[str, int]:
        """Move UI chat/progress rows from one project id to another."""
        old_project_id = str(old_project_id or "").strip() or "default"
        new_project_id = str(new_project_id or "").strip() or "default"
        counts: dict[str, int] = {}
        if old_project_id == new_project_id:
            return counts
        if await self.project_data_exists(new_project_id):
            raise ValueError(f"Project UI data already exists for {new_project_id!r}")

        for prefix in ("activity", "secretary"):
            old_channel = f"{prefix}:{old_project_id}"
            new_channel = f"{prefix}:{new_project_id}"
            cursor = await self._db.execute(
                "UPDATE messages SET channel_id = ? WHERE project_id = ? AND channel_id = ?",
                (new_channel, old_project_id, old_channel),
            )
            counts[f"messages_channel_{prefix}"] = cursor.rowcount
            cursor = await self._db.execute(
                "UPDATE channels SET channel_id = ? WHERE project_id = ? AND channel_id = ?",
                (new_channel, old_project_id, old_channel),
            )
            counts[f"channels_channel_{prefix}"] = cursor.rowcount

        for table in ("messages", "channels", "task_progress"):
            cursor = await self._db.execute(
                f"UPDATE {table} SET project_id = ? WHERE project_id = ?",
                (new_project_id, old_project_id),
            )
            counts[table] = cursor.rowcount
        await self._db.commit()
        return counts

    async def backfill_messages(
        self,
        channel_id: str,
        messages: list[dict[str, Any]],
        project_id: str = "default",
    ) -> list[dict[str, Any]]:
        """Idempotent batch insert/update for transcript messages.

        Used by the reconciliation layer to backfill CLI session history into the
        UI rendering cache.  Returns messages inserted or materially updated.
        """
        if not messages:
            return []

        cursor = await self._db.execute(
            "SELECT message_id, channel_id, sender, sender_name, content, "
            "timestamp, reply_to_id, mentions, metadata "
            "FROM messages WHERE channel_id = ? AND project_id = ? ORDER BY timestamp ASC",
            (channel_id, project_id),
        )
        existing_rows = await cursor.fetchall()
        existing_messages = [self._row_to_message_dict(row) for row in existing_rows]
        existing_ids = {message["message_id"] for message in existing_messages}
        existing_positions = {
            message["message_id"]: index
            for index, message in enumerate(existing_messages)
        }
        semantic_index = _MessageMatchIndex(
            self,
            existing_messages,
        )
        consumed_existing_ids: set[str] = set()
        inserted_messages: list[dict[str, Any]] = []
        changed_existing = False

        for raw_message in sorted(messages, key=self._message_timestamp):
            normalized_message = {
                "message_id": str(raw_message.get("message_id", "") or str(uuid.uuid4())),
                "channel_id": channel_id,
                "sender": raw_message.get("sender", "system"),
                "sender_name": raw_message.get("sender_name", ""),
                "content": self._normalize_message_content(raw_message.get("content", "")),
                "timestamp": self._message_timestamp(raw_message) or time.time(),
                "reply_to_id": raw_message.get("reply_to_id"),
                "mentions": list(raw_message.get("mentions", [])),
                "metadata": dict(raw_message.get("metadata", {}) or {}),
            }
            mid = normalized_message["message_id"]
            if mid in existing_ids:
                existing_index = existing_positions.get(mid)
                if existing_index is not None:
                    existing_match = existing_messages[existing_index]
                    # This candidate came directly from the durable transcript.
                    # For the same identity its original display body is
                    # authoritative, while metadata unique to the cache is
                    # still retained by the merge.
                    merged_existing = self._merge_duplicate_messages(
                        existing_match,
                        normalized_message,
                        prefer_candidate=True,
                    )
                    persisted, did_change = await self._persist_merged_message(
                        existing_match,
                        merged_existing,
                        channel_id=channel_id,
                        project_id=project_id,
                    )
                    if did_change:
                        semantic_index.replace(existing_index, persisted)
                        inserted_messages.append(persisted)
                        changed_existing = True
                continue

            existing_scope = await self._message_scope(mid)
            if existing_scope == (channel_id, project_id):
                # The row appeared after our initial snapshot (a live insert
                # raced this backfill). Merge in place — never re-insert the
                # same message under a scoped alias id in its own channel.
                merged = await self._merge_into_same_scope_row(
                    mid,
                    channel_id=channel_id,
                    project_id=project_id,
                    candidate=normalized_message,
                    prefer_candidate=True,
                )
                if merged is not None:
                    persisted, did_change = merged
                    existing_ids.add(mid)
                    existing_positions[mid] = semantic_index.append(persisted)
                    if did_change:
                        inserted_messages.append(persisted)
                        changed_existing = True
                    continue
            if existing_scope and existing_scope != (channel_id, project_id):
                metadata = dict(normalized_message.get("metadata", {}) or {})
                metadata.setdefault("ui_message_id", mid)
                normalized_message["metadata"] = metadata
                mid = await self._allocate_scoped_message_id(
                    mid,
                    channel_id=channel_id,
                    project_id=project_id,
                )
                normalized_message["message_id"] = mid

            duplicate_index = semantic_index.latest_match(
                normalized_message,
                excluded_message_ids=consumed_existing_ids,
            )
            if duplicate_index is not None:
                existing_duplicate = existing_messages[duplicate_index]
                existing_id = str(existing_duplicate["message_id"])
                # Prefer the authoritative transcript on an equal score, but
                # do not replace a stronger canonical result surface with a
                # lower-priority mirror.  The existing cache id and timestamp
                # remain stable either way.
                prefer_candidate = (
                    self._message_preference_score(normalized_message)
                    >= self._message_preference_score(existing_duplicate)
                )
                merged_duplicate = self._merge_duplicate_messages(
                    existing_duplicate,
                    normalized_message,
                    prefer_candidate=prefer_candidate,
                )
                persisted, did_change = await self._persist_merged_message(
                    existing_duplicate,
                    merged_duplicate,
                    channel_id=channel_id,
                    project_id=project_id,
                    preserve_timestamp=True,
                )
                if did_change:
                    semantic_index.replace(duplicate_index, persisted)
                    inserted_messages.append(persisted)
                    changed_existing = True
                consumed_existing_ids.add(existing_id)
                continue

            try:
                await self._db.execute(
                    "INSERT INTO messages "
                    "(message_id, channel_id, sender, sender_name, content, timestamp, "
                    "reply_to_id, mentions, metadata, project_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        mid,
                        channel_id,
                        normalized_message["sender"],
                        normalized_message["sender_name"],
                        normalized_message["content"],
                        normalized_message["timestamp"],
                        normalized_message["reply_to_id"],
                        json.dumps(normalized_message["mentions"]),
                        json.dumps(normalized_message["metadata"]),
                        project_id,
                    ),
                )
            except sqlite3.IntegrityError:
                merged = await self._merge_into_same_scope_row(
                    normalized_message["message_id"],
                    channel_id=channel_id,
                    project_id=project_id,
                    candidate=normalized_message,
                    prefer_candidate=True,
                )
                if merged is not None:
                    persisted, did_change = merged
                    merged_id = normalized_message["message_id"]
                    existing_ids.add(merged_id)
                    existing_positions[merged_id] = semantic_index.append(persisted)
                    if did_change:
                        inserted_messages.append(persisted)
                        changed_existing = True
                    continue
                metadata = dict(normalized_message.get("metadata", {}) or {})
                metadata.setdefault("ui_message_id", normalized_message["message_id"])
                normalized_message["metadata"] = metadata
                mid = await self._allocate_scoped_message_id(
                    normalized_message["message_id"],
                    channel_id=channel_id,
                    project_id=project_id,
                )
                normalized_message["message_id"] = mid
                await self._db.execute(
                    "INSERT INTO messages "
                    "(message_id, channel_id, sender, sender_name, content, timestamp, "
                    "reply_to_id, mentions, metadata, project_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        mid,
                        channel_id,
                        normalized_message["sender"],
                        normalized_message["sender_name"],
                        normalized_message["content"],
                        normalized_message["timestamp"],
                        normalized_message["reply_to_id"],
                        json.dumps(normalized_message["mentions"]),
                        json.dumps(normalized_message["metadata"]),
                        project_id,
                    ),
                )
            inserted_messages.append(normalized_message)
            existing_ids.add(mid)
            existing_positions[mid] = semantic_index.append({
                **normalized_message,
                "created_at": normalized_message["timestamp"],
            })
        if inserted_messages or changed_existing:
            await self._db.commit()
        return inserted_messages

    async def get_channel_messages(
        self,
        channel_id: str,
        limit: int = 100,
        project_id: str = "default",
    ) -> list[dict[str, Any]]:
        """Return messages for a specific channel."""
        fetch_limit = max(limit * 4, limit, 1)
        cursor = await self._db.execute(
            "SELECT message_id, channel_id, sender, sender_name, content, "
            "timestamp, reply_to_id, mentions, metadata "
            "FROM messages WHERE channel_id = ? AND project_id = ? ORDER BY timestamp DESC LIMIT ?",
            (channel_id, project_id, fetch_limit),
        )
        rows = await cursor.fetchall()
        messages = [self._row_to_message_dict(row) for row in rows]
        messages.reverse()
        messages = self._dedupe_messages(messages)
        if len(messages) > limit:
            messages = messages[-limit:]
        return messages

    async def get_channel_messages_page(
        self,
        channel_id: str,
        *,
        limit: int = 100,
        before_timestamp: float | None = None,
        before_message_id: str | None = None,
        detail_level: str = "full",
        project_id: str = "default",
    ) -> list[dict[str, Any]]:
        """Return the message slice from :meth:`get_channel_messages_page_info`.

        This compatibility wrapper intentionally delegates cursor handling to
        the exact pager so callers cannot accidentally paginate raw rows before
        renderer visibility and semantic de-duplication have been applied.
        """
        page = await self.get_channel_messages_page_info(
            channel_id,
            limit=limit,
            before_timestamp=before_timestamp,
            before_message_id=before_message_id,
            detail_level=detail_level,
            project_id=project_id,
        )
        return page["messages"]

    async def _get_channel_visible_messages(
        self,
        channel_id: str,
        *,
        detail_level: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Load the final UI-visible, de-duplicated channel timeline.

        The cache stores both transcript backfill and UI-only rows such as
        approval cards and legacy notices.  SQL can exclude detail-only rows,
        but only the message formatter's semantic merge can determine the
        final rows.  Consequently the merge must happen across the complete
        visible set before a page boundary is chosen.
        """
        query = (
            "SELECT message_id, channel_id, sender, sender_name, content, "
            "timestamp, reply_to_id, mentions, metadata "
            "FROM messages WHERE channel_id = ? AND project_id = ?"
        )
        query += rendered_transcript_visibility_sql(
            detail_level=detail_level,
        )
        query += " ORDER BY timestamp ASC, message_id ASC"
        cursor = await self._db.execute(query, (channel_id, project_id))
        rows = await cursor.fetchall()
        messages = [self._row_to_message_dict(row) for row in rows]
        deduped = self._dedupe_messages(messages)
        return sorted(
            deduped,
            key=lambda message: (
                self._message_timestamp(message),
                str(message.get("message_id", "") or ""),
            ),
        )

    async def get_channel_messages_page_info(
        self,
        channel_id: str,
        *,
        limit: int = 100,
        before_timestamp: float | None = None,
        before_message_id: str | None = None,
        detail_level: str = "full",
        project_id: str = "default",
    ) -> dict[str, Any]:
        """Return an exact final-visible page and its pagination metadata.

        ``total_count`` counts the de-duplicated UI rows for the whole channel;
        ``has_more`` describes rows older than the returned page for the given
        cursor.  Both values include UI-only messages that have no transcript
        counterpart.
        """
        messages = await self._get_channel_visible_messages(
            channel_id,
            detail_level=detail_level,
            project_id=project_id,
        )
        total_count = len(messages)
        candidates = messages
        normalized_before_id = str(before_message_id or "").strip()
        if before_timestamp is not None:
            normalized_before_timestamp = float(before_timestamp)
            if normalized_before_id:
                candidates = [
                    message
                    for message in messages
                    if (
                        self._message_timestamp(message),
                        str(message.get("message_id", "") or ""),
                    ) < (normalized_before_timestamp, normalized_before_id)
                ]
            else:
                candidates = [
                    message
                    for message in messages
                    if self._message_timestamp(message) < normalized_before_timestamp
                ]
        normalized_limit = max(int(limit), 1)
        return {
            "messages": candidates[-normalized_limit:],
            "has_more": len(candidates) > normalized_limit,
            "total_count": total_count,
        }

    async def get_channel_visible_message_count(
        self,
        channel_id: str,
        project_id: str = "default",
        *,
        detail_level: str = "full",
    ) -> int:
        """Return the de-duplicated visible message count for a channel."""
        messages = await self._get_channel_visible_messages(
            channel_id,
            detail_level=detail_level,
            project_id=project_id,
        )
        return len(messages)

    async def get_unresolved_checkpoint_messages(
        self,
        channel_id: str,
        *,
        checkpoint_type: str | None = None,
        project_id: str = "default",
    ) -> list[dict[str, Any]]:
        """Return checkpoint cards that still have no terminal UI status."""
        normalized_checkpoint_type = str(checkpoint_type or "").strip()
        terminal_statuses = (
            "responded",
            "resolved",
            "timeout",
            "timed_out",
            "expired",
            "stale",
            "superseded",
            "ignored",
            "cancelled",
            "canceled",
            "invalid",
        )
        placeholders = ",".join("?" for _ in terminal_statuses)
        query = (
            "SELECT message_id, channel_id, sender, sender_name, content, "
            "timestamp, reply_to_id, mentions, metadata "
            "FROM messages WHERE channel_id = ? AND project_id = ? "
            "AND COALESCE(json_extract(metadata, '$.checkpoint_id'), '') != '' "
            f"AND lower(COALESCE(json_extract(metadata, '$.checkpoint_status'), '')) NOT IN ({placeholders})"
        )
        params: list[Any] = [channel_id, project_id, *terminal_statuses]
        if normalized_checkpoint_type:
            query += " AND json_extract(metadata, '$.checkpoint_type') = ?"
            params.append(normalized_checkpoint_type)
        query += " ORDER BY timestamp ASC"
        cursor = await self._db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_message_dict(row) for row in rows]

    async def mark_checkpoint_responded(
        self,
        channel_id: str,
        checkpoint_id: str,
        *,
        checkpoint_type: str | None = None,
        response_message_id: str | None = None,
        response_metadata: dict[str, Any] | None = None,
        project_id: str = "default",
    ) -> dict[str, Any] | None:
        """Persist that a checkpoint card already received a user response.

        Returns the full updated message dict if the checkpoint was found and
        updated, or ``None`` otherwise.  The returned dict has the same shape
        as :meth:`insert_message` / :meth:`_row_to_message_dict` so it can be
        broadcast directly via the WebSocket ``session_message`` event.
        """
        return await self.update_checkpoint_status(
            checkpoint_id,
            channel_id=channel_id,
            checkpoint_type=checkpoint_type,
            status="responded",
            response_message_id=response_message_id,
            response_metadata=response_metadata,
            project_id=project_id,
        )

    async def get_checkpoint_message(
        self,
        checkpoint_id: str,
        *,
        channel_id: str | None = None,
        checkpoint_type: str | None = None,
        project_id: str = "default",
    ) -> dict[str, Any] | None:
        """Read-only lookup of a checkpoint card message by checkpoint id."""
        normalized_checkpoint_id = str(checkpoint_id or "").strip()
        if not normalized_checkpoint_id:
            return None
        normalized_checkpoint_type = str(checkpoint_type or "").strip()
        normalized_channel_id = str(channel_id or "").strip()
        params: list[Any] = [project_id]
        query = (
            "SELECT message_id, channel_id, sender, sender_name, content, "
            "timestamp, reply_to_id, mentions, metadata "
            "FROM messages WHERE project_id = ?"
        )
        if normalized_channel_id:
            query += " AND channel_id = ?"
            params.append(normalized_channel_id)
        query += " ORDER BY timestamp DESC"
        cursor = await self._db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        for row in rows:
            metadata = json.loads(row[8]) if row[8] else {}
            if str(metadata.get("checkpoint_id", "")).strip() != normalized_checkpoint_id:
                continue
            if normalized_checkpoint_type and str(metadata.get("checkpoint_type", "")).strip() != normalized_checkpoint_type:
                continue
            return self._row_to_message_dict(row)
        return None

    async def update_checkpoint_status(
        self,
        checkpoint_id: str,
        *,
        channel_id: str | None = None,
        checkpoint_type: str | None = None,
        status: str = "resolved",
        response_message_id: str | None = None,
        response_metadata: dict[str, Any] | None = None,
        status_metadata: dict[str, Any] | None = None,
        project_id: str = "default",
    ) -> dict[str, Any] | None:
        """Persist a terminal checkpoint status on the original card.

        ``channel_id`` is optional so lifecycle events such as escalation
        timeout/resolved, which only carry the checkpoint id, can still update
        the original session message across any project/session.
        """
        normalized_checkpoint_id = str(checkpoint_id or "").strip()
        normalized_checkpoint_type = str(checkpoint_type or "").strip()
        normalized_channel_id = str(channel_id or "").strip()
        normalized_status = str(status or "resolved").strip().lower() or "resolved"
        if not normalized_checkpoint_id:
            return None

        params: list[Any] = [project_id]
        query = (
            "SELECT message_id, channel_id, sender, sender_name, content, "
            "timestamp, reply_to_id, mentions, metadata "
            "FROM messages WHERE project_id = ?"
        )
        if normalized_channel_id:
            query += " AND channel_id = ?"
            params.append(normalized_channel_id)
        query += " ORDER BY timestamp DESC"
        cursor = await self._db.execute(
            query,
            tuple(params),
        )
        rows = await cursor.fetchall()
        for row in rows:
            message_id = row[0]
            metadata = json.loads(row[8]) if row[8] else {}
            if str(metadata.get("checkpoint_id", "")).strip() != normalized_checkpoint_id:
                continue
            if normalized_checkpoint_type and str(metadata.get("checkpoint_type", "")).strip() != normalized_checkpoint_type:
                continue

            current_status = str(metadata.get("checkpoint_status", "") or "").strip().lower()
            terminal_statuses = {
                "responded",
                "resolved",
                "timeout",
                "timed_out",
                "expired",
                "stale",
                "superseded",
                "ignored",
                "cancelled",
                "canceled",
                "invalid",
            }
            if current_status in terminal_statuses and normalized_status != "responded":
                return {
                    "message_id": message_id,
                    "channel_id": row[1],
                    "sender": row[2],
                    "sender_name": row[3],
                    "content": self._normalize_message_content(row[4]),
                    "created_at": row[5],
                    "reply_to_id": row[6],
                    "mentions": json.loads(row[7]) if row[7] else [],
                    "metadata": metadata,
                    "project_id": project_id,
                }

            now = time.time()
            metadata["checkpoint_status"] = normalized_status
            if normalized_status == "responded":
                metadata["checkpoint_responded_at"] = now
            else:
                metadata["checkpoint_resolved_at"] = now
            if response_message_id:
                metadata["checkpoint_response_message_id"] = response_message_id
            if isinstance(status_metadata, dict):
                for key, value in status_metadata.items():
                    metadata[str(key)] = value
            if isinstance(response_metadata, dict):
                raw_checkpoint_reply_kind = str(response_metadata.get("checkpoint_reply_kind", "") or "").strip().lower()
                if raw_checkpoint_reply_kind in {"approve", "deny", "feedback", "ignore"}:
                    metadata["checkpoint_reply_kind"] = raw_checkpoint_reply_kind
                raw_role_agents = response_metadata.get("recruitment_role_agents")
                if isinstance(raw_role_agents, dict):
                    normalized_role_agents = {
                        str(raw_role_id or "").strip(): str(raw_agent or "").strip().lower()
                        for raw_role_id, raw_agent in raw_role_agents.items()
                        if str(raw_role_id or "").strip() and str(raw_agent or "").strip()
                    }
                    if normalized_role_agents:
                        metadata["recruitment_role_agents"] = normalized_role_agents
                        raw_proposals = metadata.get("proposals")
                        if isinstance(raw_proposals, list):
                            updated_proposals: list[Any] = []
                            proposals_changed = False
                            for proposal in raw_proposals:
                                if not isinstance(proposal, dict):
                                    updated_proposals.append(proposal)
                                    continue
                                role_id = str(proposal.get("role_id", "")).strip()
                                next_agent = normalized_role_agents.get(role_id)
                                if not next_agent:
                                    updated_proposals.append(proposal)
                                    continue
                                current_agent = str(proposal.get("selected_agent", "") or "").strip().lower()
                                if current_agent == next_agent:
                                    updated_proposals.append(proposal)
                                    continue
                                proposals_changed = True
                                updated_proposals.append({
                                    **proposal,
                                    "selected_agent": next_agent,
                                })
                            if proposals_changed:
                                metadata["proposals"] = updated_proposals
                        raw_staffing_roles = metadata.get("staffing_roles")
                        if isinstance(raw_staffing_roles, list):
                            updated_staffing_roles: list[Any] = []
                            staffing_roles_changed = False
                            for role in raw_staffing_roles:
                                if not isinstance(role, dict):
                                    updated_staffing_roles.append(role)
                                    continue
                                role_id = str(role.get("role_id", "")).strip()
                                next_agent = normalized_role_agents.get(role_id)
                                if not next_agent:
                                    updated_staffing_roles.append(role)
                                    continue
                                current_agent = str(role.get("selected_agent", "") or "").strip().lower()
                                if current_agent == next_agent:
                                    updated_staffing_roles.append(role)
                                    continue
                                staffing_roles_changed = True
                                updated_staffing_roles.append({
                                    **role,
                                    "selected_agent": next_agent,
                                })
                            if staffing_roles_changed:
                                metadata["staffing_roles"] = updated_staffing_roles
                raw_recruitment_agent = str(response_metadata.get("recruitment_agent", "") or "").strip().lower().replace("-", "_")
                if raw_recruitment_agent:
                    metadata["recruitment_agent"] = raw_recruitment_agent
                raw_staffing_action = str(response_metadata.get("staffing_action", "") or "").strip().lower()
                if raw_staffing_action:
                    metadata["staffing_action"] = raw_staffing_action
                raw_staffing_selections = response_metadata.get("staffing_selections")
                if isinstance(raw_staffing_selections, dict):
                    normalized_staffing_selections: dict[str, dict[str, str]] = {}
                    for raw_role_id, raw_selection in raw_staffing_selections.items():
                        role_id = str(raw_role_id or "").strip()
                        if not role_id or not isinstance(raw_selection, dict):
                            continue
                        kind = str(raw_selection.get("kind", "") or "").strip().lower()
                        selected_id = str(raw_selection.get("id", "") or "").strip()
                        if kind in {"employee", "template"} and selected_id:
                            normalized_staffing_selections[role_id] = {"kind": kind, "id": selected_id}
                        elif kind == "fallback":
                            normalized_staffing_selections[role_id] = {"kind": "fallback", "id": ""}
                    if normalized_staffing_selections:
                        metadata["staffing_selections"] = normalized_staffing_selections
            await self._db.execute(
                "UPDATE messages SET metadata = ? WHERE message_id = ? AND project_id = ?",
                (json.dumps(metadata, ensure_ascii=False), message_id, project_id),
            )
            await self._db.commit()
            return {
                "message_id": message_id,
                "channel_id": row[1],
                "sender": row[2],
                "sender_name": row[3],
                "content": self._normalize_message_content(row[4]),
                "created_at": row[5],
                "reply_to_id": row[6],
                "mentions": json.loads(row[7]) if row[7] else [],
                "metadata": metadata,
                "project_id": project_id,
            }
        return None

    # ── Task progress methods ─────────────────────────────────────────

    # Cap on how many persisted progress entries we keep per task. The UI's
    # Activity detail panel (AgentWorkPanel) reads this back on page
    # refresh / reconnect, and a busy role can easily emit >50 entries in a
    # single work item (start + several thinking chunks + many tool_call lines +
    # gate verdict). 50 was too aggressive — role activity panels showed
    # "start/thinking/tool/gate" events only partially. Entries are small
    # dicts (~200B typical, ~1KB worst case with thinking detail), so 1000
    # adds at most ~1MB per task in SQLite. If this ever becomes a storage
    # concern, swap this in-row JSON blob for a proper rolling table.
    _PROGRESS_MAX_ENTRIES = 1000

    # Streaming text types arrive as one entry per token-sized delta. Without
    # folding, thinking floods the entry cap (forensics: 929/1000 entries were
    # single-token thinking rows) and evicts the interleaved tool history.
    _PROGRESS_STREAM_MERGE_TYPES = frozenset({"thinking", "assistant"})

    @staticmethod
    def _progress_stream_key(entry: dict[str, Any]) -> tuple[str, str, str] | None:
        entry_type = str(entry.get("type", "") or "")
        if entry_type not in ChatStore._PROGRESS_STREAM_MERGE_TYPES:
            return None
        item_id = str(entry.get("item_id") or entry.get("stream_id") or "").strip()
        if not item_id:
            return None
        return (entry_type, str(entry.get("turn_id", "") or ""), item_id)

    @classmethod
    def _fold_progress_entries(
        cls,
        existing: list[dict[str, Any]],
        new_entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Fold streaming deltas into their stream's entry (mirrors the
        frontend ``appendProgressEntry`` merge so persisted state equals what
        the live client built)."""
        merged = list(existing)
        index_by_key: dict[tuple[str, str, str], int] = {}
        for i, entry in enumerate(merged):
            key = cls._progress_stream_key(entry)
            if key is not None:
                index_by_key[key] = i
        for entry in new_entries:
            key = cls._progress_stream_key(entry)
            if key is None or key not in index_by_key:
                if key is not None:
                    index_by_key[key] = len(merged)
                merged.append(entry)
                continue
            target = merged[index_by_key[key]]
            last_seq = target.get("seq")
            new_seq = entry.get("seq")
            if isinstance(last_seq, (int, float)) and isinstance(new_seq, (int, float)) and new_seq <= last_seq:
                continue
            # Deltas are disjoint token fragments — concatenate raw, no strip.
            detail = f"{target.get('detail') or ''}{entry.get('detail') or ''}"
            preview = " ".join(detail.split())
            folded = dict(target)
            folded.update(entry)
            # The folded stream is one UI timeline row. Preserve its creation
            # timestamp so reconnect snapshots cannot move it around tools.
            folded["timestamp"] = target.get("timestamp", entry.get("timestamp"))
            folded["detail"] = detail
            folded["summary"] = preview[:120].rstrip() + ("..." if len(preview) > 120 else "")
            merged[index_by_key[key]] = folded
        return merged

    async def append_progress(
        self,
        task_id: str,
        new_entries: list[dict[str, Any]],
        project_id: str = "default",
    ) -> None:
        """Merge new progress entries into the persisted list for a task.

        Keeps at most ``_PROGRESS_MAX_ENTRIES`` (most recent). Uses UPSERT so
        the first call creates the row and subsequent calls update it.
        """
        existing = await self.get_progress(task_id, project_id=project_id)
        merged = self._fold_progress_entries(existing, new_entries)[-self._PROGRESS_MAX_ENTRIES:]

        async def _write() -> None:
            await self._db.execute(
                "INSERT OR REPLACE INTO task_progress (task_id, entries, updated_at, project_id) "
                "VALUES (?, ?, ?, ?)",
                (task_id, json.dumps(merged, ensure_ascii=False, default=str), time.time(), project_id),
            )
            await self._db.commit()

        await self._retry_locked(_write)

    async def get_progress(self, task_id: str, project_id: str = "default") -> list[dict[str, Any]]:
        """Read persisted progress entries for a task."""
        cursor = await self._db.execute(
            "SELECT entries FROM task_progress WHERE task_id = ? AND project_id = ?",
            (task_id, project_id),
        )
        row = await cursor.fetchone()
        if not row:
            return []
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return []

    async def get_progress_many(
        self,
        task_ids: list[str],
        project_id: str = "default",
    ) -> dict[str, list[dict[str, Any]]]:
        """Read persisted progress entries for many tasks in one query."""
        normalized_ids = [str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()]
        if not normalized_ids:
            return {}

        placeholders = ",".join("?" for _ in normalized_ids)
        cursor = await self._db.execute(
            f"SELECT task_id, entries FROM task_progress WHERE task_id IN ({placeholders}) AND project_id = ?",
            tuple([*normalized_ids, project_id]),
        )
        rows = await cursor.fetchall()
        progress_by_task: dict[str, list[dict[str, Any]]] = {
            task_id: []
            for task_id in normalized_ids
        }
        for task_id, raw_entries in rows:
            try:
                progress_by_task[str(task_id)] = json.loads(raw_entries) if raw_entries else []
            except (json.JSONDecodeError, TypeError):
                progress_by_task[str(task_id)] = []
        return progress_by_task

    async def delete_progress(self, task_id: str, project_id: str = "default") -> None:
        """Remove progress entries for a task (called on session delete)."""
        await self._db.execute(
            "DELETE FROM task_progress WHERE task_id = ? AND project_id = ?",
            (task_id, project_id),
        )
        await self._db.commit()

    async def delete_project_progress(self, project_id: str) -> None:
        """Remove all progress entries for a project."""
        await self._db.execute(
            "DELETE FROM task_progress WHERE project_id = ?", (project_id,),
        )
        await self._db.commit()
