"""Unit tests for WorkItemContextView — work-item read adapter.

Verifies the precedence rules (work_item.metadata first, task.metadata
fallback), defensive copies for list / dict returns, and graceful
degradation when either side is missing.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from opc.layer2_organization.work_item_context_view import WorkItemContextView


def _wi(metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(metadata=metadata)


def _task(metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(metadata=metadata)


class WorkItemContextViewTests(unittest.TestCase):
    def test_prefers_work_item_metadata_when_both_present(self) -> None:
        view = WorkItemContextView(
            work_item=_wi({"role": "senior_engineer", "shared": "wi-wins"}),
            task=_task({"role": "legacy_role", "shared": "task-loses"}),
        )
        self.assertEqual(view.get("role"), "senior_engineer")
        self.assertEqual(view.get("shared"), "wi-wins")

    def test_falls_back_to_task_metadata_when_wi_key_missing(self) -> None:
        view = WorkItemContextView(
            work_item=_wi({"role": "engineer"}),
            task=_task({"work_item_role_name": "Senior Engineer"}),
        )
        self.assertEqual(view.get("role"), "engineer")
        self.assertEqual(view.get("work_item_role_name"), "Senior Engineer")

    def test_returns_default_when_neither_present(self) -> None:
        view = WorkItemContextView(work_item=_wi({}), task=_task({}))
        self.assertIsNone(view.get("missing"))
        self.assertEqual(view.get("missing", "fallback"), "fallback")

    def test_construct_from_none_work_item_degrades_to_task_only(self) -> None:
        """task-mode path: no work_item linked. View must not crash and
        must serve all values from task.metadata."""
        view = WorkItemContextView(work_item=None, task=_task({"key": "val"}))
        self.assertEqual(view.get("key"), "val")
        self.assertEqual(view.get("missing", "d"), "d")

    def test_construct_from_none_task_degrades_to_wi_only(self) -> None:
        """company-mode path where caller only has the work_item. View
        serves everything from work_item.metadata."""
        view = WorkItemContextView(work_item=_wi({"key": "val"}), task=None)
        self.assertEqual(view.get("key"), "val")

    def test_both_none_is_empty_view(self) -> None:
        view = WorkItemContextView()
        self.assertFalse(view.has("anything"))
        self.assertEqual(view.get("anything", "d"), "d")

    def test_get_list_returns_copy_not_reference(self) -> None:
        """Callers must not be able to accidentally mutate the source
        through the returned list."""
        source = ["a", "b", "c"]
        view = WorkItemContextView(work_item=_wi({"items": source}))
        returned = view.get_list("items")
        returned.append("d")
        self.assertEqual(source, ["a", "b", "c"])  # unchanged
        self.assertEqual(view.get_list("items"), ["a", "b", "c"])  # view still clean

    def test_get_list_coerces_tuple_to_list(self) -> None:
        view = WorkItemContextView(work_item=_wi({"items": ("a", "b")}))
        self.assertEqual(view.get_list("items"), ["a", "b"])

    def test_get_list_missing_or_wrong_type_returns_empty(self) -> None:
        view = WorkItemContextView(work_item=_wi({"k": "not a list"}))
        self.assertEqual(view.get_list("k"), [])
        self.assertEqual(view.get_list("missing"), [])

    def test_get_dict_returns_copy_not_reference(self) -> None:
        source = {"a": 1, "b": 2}
        view = WorkItemContextView(work_item=_wi({"m": source}))
        returned = view.get_dict("m")
        returned["c"] = 3
        self.assertEqual(source, {"a": 1, "b": 2})
        self.assertEqual(view.get_dict("m"), {"a": 1, "b": 2})

    def test_get_dict_missing_or_wrong_type_returns_empty(self) -> None:
        view = WorkItemContextView(work_item=_wi({"k": "scalar"}))
        self.assertEqual(view.get_dict("k"), {})
        self.assertEqual(view.get_dict("missing"), {})

    def test_has_detects_visible_keys_only(self) -> None:
        view = WorkItemContextView(
            work_item=_wi({"wi_only": 1}),
            task=_task({"work_item_role_name": "legacy visible", "task_only": 2}),
        )
        self.assertTrue(view.has("wi_only"))
        self.assertTrue(view.has("work_item_role_name"))
        self.assertFalse(view.has("task_only"))
        self.assertFalse(view.has("neither"))

    def test_company_view_hides_unknown_task_fallback_keys(self) -> None:
        view = WorkItemContextView(
            work_item=_wi({}),
            task=_task({"task_only": "hidden", "progress_log": ["legacy"]}),
        )
        self.assertEqual(view.get("task_only", "default"), "default")
        self.assertEqual(view.get_list("progress_log"), ["legacy"])

    def test_none_valued_wi_key_not_treated_as_missing(self) -> None:
        """dict.get semantics: if work_item has the key with value None,
        the view returns None — it does NOT fall through to task.metadata.
        This is the same rule the original task.metadata.get() used, so
        migrated callers see identical behaviour."""
        view = WorkItemContextView(
            work_item=_wi({"k": None}),
            task=_task({"k": "task_value"}),
        )
        self.assertIsNone(view.get("k"))

    def test_object_without_metadata_attribute_degrades_gracefully(self) -> None:
        """Defensive: passing an object that lacks .metadata (or has it as
        non-dict) should not crash — view becomes empty on that side."""
        weird = SimpleNamespace(metadata="not a dict")
        view = WorkItemContextView(work_item=weird, task=_task({"k": "v"}))
        self.assertEqual(view.get("k"), "v")

    def test_mutating_source_dict_after_construction_does_not_affect_view(self) -> None:
        """Construction snapshots the dicts (shallow copy). If the source
        dict changes later, the view keeps its construction-time snapshot."""
        src = {"key": "initial"}
        view = WorkItemContextView(work_item=_wi(src))
        src["key"] = "mutated"
        self.assertEqual(view.get("key"), "initial")


if __name__ == "__main__":
    unittest.main()
