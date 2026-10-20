# AutoSwarm CLI Board

`autoswarm board` provides a full-screen terminal command center for AutoSwarm.

## Install

The board uses the optional `textual` dependency.

```bash
pip install "autoswarm[cli-board]"
```

## Run

```bash
autoswarm board
autoswarm board --project demo
autoswarm board --project demo --refresh-interval 1.5
```

## Interaction Model

- The board is a session-first TUI: one visible card maps to one user-facing task/session.
- The layout is split into `Session Rail | Main Viewport | Context Dock`, with a top metrics bar and a bottom status bar.
- The main viewport supports `kanban`, `list`, and `focus` modes so you can switch between orchestration, scanning, and deep inspection.
- Engine-created internal tasks that point back to an origin task via `metadata.origin_task_id` are hidden from the main board and surfaced under the selected task's "linked executions" detail section.
- In-process updates come from `EventBus` plus `engine.on_progress`.
- Cross-process consistency comes from a periodic reconcile loop that reloads the authoritative SQLite task/session data.

## Main UI Regions

- `Metrics Bar`: board health, pipeline counts, alerts, active selection, filter state.
- `Session Rail`: recent live sessions, queued work, archived sessions, checkpoint badges.
- `Main Viewport`: kanban board, dense task list, or focused task console.
- `Context Dock`: `Detail`, `Session`, and `Activity` tabs.
- `Status Bar`: current mode, pane focus, selection, and quick key reminders.

## Key Bindings

- Arrow keys / `h` `j` `k` `l`: move selection
- `Tab` / `Shift+Tab`: cycle pane focus
- `Enter`: open focus view or advance the context dock
- `1`: Kanban view
- `2`: List view
- `3`: Focus view
- `Space`: toggle density
- `Ctrl+K` or `:`: open command palette
- `n`: create task
- `g`: run selected task prompt
- `s`: send session reply
- `m`: move task between columns
- `a`: approve pending checkpoint
- `d`: deny pending checkpoint
- `c`: mark task done
- `x`: cancel task
- `t`: rerun task
- `/`: set search filter
- `f`: toggle done visibility
- `r`: refresh
- `?`: help
- `q`: quit

## Notes

- `focus` mode collapses the left session rail and turns the selected task into a larger console-style view.
- The context dock keeps three perspectives on the same task: structured metadata, transcript/progress, and board alerts/live runtime.
- The command palette is useful when you forget a key or want a single launcher for common actions.

