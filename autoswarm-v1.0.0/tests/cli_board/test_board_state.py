from __future__ import annotations

import unittest

from opc.plugins.cli_board.state.models import BoardSnapshot, BoardTaskView, PendingCheckpointView
from opc.plugins.cli_board.state.store import BoardStateStore


def _task(task_id: str, title: str, column_id: str, *, status: str | None = None) -> BoardTaskView:
    return BoardTaskView(
        task_id=task_id,
        title=title,
        description=f"{title} description",
        status=status or ("pending" if column_id == "todo" else "running"),
        column_id=column_id,
        priority="medium",
        created_at=1.0,
        updated_at=1.0,
    )


class BoardStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = BoardStateStore()
        self.store.replace_snapshot(
            BoardSnapshot(
                project_id="demo",
                tasks=[
                    _task("todo-1", "Todo 1", "todo"),
                    _task("todo-2", "Todo 2", "todo"),
                    _task("run-1", "Run 1", "in-progress"),
                    _task("done-1", "Done 1", "done", status="done"),
                ],
            )
        )

    def test_initial_selection_uses_first_visible_task(self) -> None:
        selected = self.store.selected_task()
        self.assertIsNotNone(selected)
        self.assertEqual(selected.task_id, "todo-1")

    def test_move_selection_crosses_columns_and_rows(self) -> None:
        self.store.move_selection(row_delta=1)
        self.assertEqual(self.store.selected_task().task_id, "todo-2")

        self.store.move_selection(column_delta=1)
        self.assertEqual(self.store.selected_task().task_id, "run-1")

        self.store.move_selection(column_delta=1)
        self.assertEqual(self.store.selected_task().task_id, "done-1")

    def test_toggle_done_hides_done_column_tasks(self) -> None:
        showing = self.store.toggle_show_done()
        self.assertFalse(showing)
        counts = self.store.board_counts()
        self.assertEqual(counts["done"], 0)

    def test_search_filter_updates_selection(self) -> None:
        self.store.set_search_query("run")
        selected = self.store.selected_task()
        self.assertIsNotNone(selected)
        self.assertEqual(selected.task_id, "run-1")

    def test_runtime_updates_and_progress_are_tracked(self) -> None:
        self.store.apply_runtime_update(
            "run-1",
            status="tool_active",
            current_tool="shell_exec",
            iteration=2,
            tool_elapsed_ms=420,
            last_tool_summary="pytest -q completed",
            context_tokens=1200,
            context_window=4000,
            context_remaining_pct=70,
            turn_cost_usd=0.0123,
            session_cost_usd=0.0456,
            pending_permission_count=1,
            drain_mode="smooth",
        )
        self.store.append_progress("run-1", "[Tool: shell_exec] pytest -q")

        runtime = self.store.runtime_for("run-1")
        self.assertIsNotNone(runtime)
        self.assertEqual(runtime.status, "tool_active")
        self.assertEqual(runtime.current_tool, "shell_exec")
        self.assertEqual(runtime.iteration, 2)
        self.assertEqual(runtime.tool_elapsed_ms, 420)
        self.assertEqual(runtime.last_tool_summary, "pytest -q completed")
        self.assertEqual(runtime.context_remaining_pct, 70)
        self.assertEqual(runtime.turn_cost_usd, 0.0123)
        self.assertEqual(runtime.pending_permission_count, 1)
        self.assertEqual(runtime.drain_mode, "smooth")
        self.assertEqual(len(runtime.progress_entries), 1)

    def test_metrics_and_alerts_include_snapshot_and_runtime_state(self) -> None:
        self.store.snapshot.hidden_task_count = 2
        self.store.snapshot.pending_checkpoint_count = 1
        self.store.snapshot.tasks[2].pending_checkpoint = PendingCheckpointView(
            checkpoint_id="cp-1",
            checkpoint_type="task_user_input",
            status="pending",
            session_id="session-run-1",
            task_id="run-1",
            summary="Need approval",
            prompt="Approve this step",
        )
        self.store.apply_runtime_update("run-1", status="tool_active", current_tool="shell_exec")

        metrics = self.store.metrics()
        alerts = self.store.alerts()

        self.assertEqual(metrics.total_tasks, 6)
        self.assertEqual(metrics.pending_checkpoint_count, 1)
        self.assertEqual(metrics.running_count, 1)
        self.assertGreaterEqual(metrics.alert_count, 1)
        self.assertTrue(any(alert.task_id == "run-1" for alert in alerts))

    def test_session_navigation_and_view_state_controls(self) -> None:
        self.store.select_task("run-1")
        self.store.set_pane_focus("session-rail")
        self.store.move_session_selection(1)
        self.assertEqual(self.store.selected_task().task_id, "todo-1")

        self.store.set_view_mode("list")
        self.assertEqual(self.store.view_mode, "list")
        self.assertEqual(self.store.toggle_density(), "comfortable")
        self.assertEqual(self.store.cycle_context_tab(1), "session")
