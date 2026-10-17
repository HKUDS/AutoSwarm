"""Safely recover a stuck AutoSwarm task/session when the UI becomes unresponsive.

Typical symptoms it fixes:
  - The Stop button does nothing and you cannot send new messages.
  - A task is stuck in `running` with a dangling execution_lock after a
    disconnect or server crash.
  - A `runtime_sessions` row remains in status=running after the client
    left, blocking fresh activity on the same task.

Usage:
    # Inspect: show what looks stuck in a project (no changes applied)
    python scripts/reset_stuck_task.py --project new10

    # Inspect a specific session or task
    python scripts/reset_stuck_task.py --project new10 --session app06
    python scripts/reset_stuck_task.py --project new10 --task-id 5d8ee32e-...

    # Actually apply the reset
    python scripts/reset_stuck_task.py --project new10 --session app06 --apply

    # Project-wide recovery after a crash
    python scripts/reset_stuck_task.py --project new10 --all --apply

Exit codes: 0 on success / clean inspection, 2 on usage error, 3 if the
project database cannot be opened.

IMPORTANT: the AutoSwarm server keeps in-memory state (asyncio locks,
session->task maps, background tasks). If the server process is still
running against the same DB, also restart it or reload the browser tab
after this script prints the "next steps" block. DB edits alone will not
release an orphaned asyncio.Lock in a live server.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _opc_home() -> Path:
    env = os.environ.get("OPC_HOME")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".opc").exists():
            return candidate / ".opc"
    return Path.cwd() / ".opc"


def _project_db_path(project_id: str) -> Path:
    if project_id == "__global__":
        return _opc_home() / "global.db"
    return _opc_home() / "projects" / project_id / "tasks.db"


@dataclass
class StuckRow:
    kind: str
    identifier: str
    status: str
    extra: str = ""

    def render(self) -> str:
        base = f"  [{self.kind}] {self.identifier}  status={self.status}"
        return f"{base}  {self.extra}" if self.extra else base


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _resolve_targets(
    conn: sqlite3.Connection,
    *,
    task_id: str | None,
    session_id_or_title: str | None,
    include_all: bool,
) -> tuple[set[str], set[str]]:
    """Return (task_ids, session_ids) that match user-provided filters."""
    task_ids: set[str] = set()
    session_ids: set[str] = set()

    if task_id:
        task_ids.add(task_id)
        row = conn.execute(
            "SELECT session_id FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row and row[0]:
            session_ids.add(row[0])

    if session_id_or_title:
        rows = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ? OR title = ?",
            (session_id_or_title, session_id_or_title),
        ).fetchall()
        for (sid,) in rows:
            session_ids.add(sid)
        if not rows:
            session_ids.add(session_id_or_title)
        for sid in list(session_ids):
            for (tid,) in conn.execute(
                "SELECT id FROM tasks WHERE session_id = ?", (sid,)
            ).fetchall():
                task_ids.add(tid)

    if include_all:
        task_ids.update(
            tid for (tid,) in conn.execute(
                "SELECT id FROM tasks WHERE status = 'running' OR execution_lock = 1"
            ).fetchall()
        )
        session_ids.update(
            sid for (sid,) in conn.execute(
                "SELECT DISTINCT session_id FROM runtime_sessions WHERE status = 'running'"
            ).fetchall() if sid
        )

    return task_ids, session_ids


def _scan_stuck(
    conn: sqlite3.Connection,
    task_ids: set[str],
    session_ids: set[str],
) -> list[StuckRow]:
    findings: list[StuckRow] = []

    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        for (tid, status, lock, locked_at, title) in conn.execute(
            f"SELECT id, status, execution_lock, execution_locked_at, title "
            f"FROM tasks WHERE id IN ({placeholders})",
            tuple(task_ids),
        ).fetchall():
            flags = []
            if status == "running":
                flags.append("status=running")
            if lock:
                flags.append(f"execution_lock=1 (since {locked_at or '?'})")
            if flags:
                findings.append(
                    StuckRow("task", tid, status, f"'{title}'  " + ", ".join(flags))
                )

    combined_sessions: set[str] = set(session_ids)
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        for (sid,) in conn.execute(
            f"SELECT DISTINCT session_id FROM runtime_sessions "
            f"WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        ).fetchall():
            if sid:
                combined_sessions.add(sid)

    if combined_sessions:
        placeholders = ",".join("?" * len(combined_sessions))
        for (rsid, sid, tid, status, updated_at) in conn.execute(
            f"SELECT runtime_session_id, session_id, task_id, status, updated_at "
            f"FROM runtime_sessions WHERE session_id IN ({placeholders}) AND status = 'running'",
            tuple(combined_sessions),
        ).fetchall():
            findings.append(
                StuckRow(
                    "runtime_session",
                    rsid,
                    status,
                    f"session={sid} task={tid} updated={updated_at}",
                )
            )

    return findings


def _apply_reset(
    conn: sqlite3.Connection,
    task_ids: set[str],
    session_ids: set[str],
) -> dict[str, int]:
    now = _utc_now_iso()
    counts = {"tasks_reset": 0, "locks_cleared": 0, "runtime_sessions_ended": 0}

    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        cur = conn.execute(
            f"UPDATE tasks SET status = 'pending' "
            f"WHERE id IN ({placeholders}) AND status = 'running'",
            tuple(task_ids),
        )
        counts["tasks_reset"] = cur.rowcount or 0

        cur = conn.execute(
            f"UPDATE tasks SET execution_lock = 0, execution_locked_at = NULL "
            f"WHERE id IN ({placeholders}) AND execution_lock = 1",
            tuple(task_ids),
        )
        counts["locks_cleared"] = cur.rowcount or 0

    combined_sessions: set[str] = set(session_ids)
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        for (sid,) in conn.execute(
            f"SELECT DISTINCT session_id FROM runtime_sessions "
            f"WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        ).fetchall():
            if sid:
                combined_sessions.add(sid)

    if combined_sessions:
        placeholders = ",".join("?" * len(combined_sessions))
        cur = conn.execute(
            f"UPDATE runtime_sessions SET status = 'ended', updated_at = ? "
            f"WHERE session_id IN ({placeholders}) AND status = 'running'",
            (now, *combined_sessions),
        )
        counts["runtime_sessions_ended"] = cur.rowcount or 0

    conn.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="project_id (subdir under .opc/projects)")
    parser.add_argument("--session", dest="session", help="session_id OR session title (e.g. 'app06')")
    parser.add_argument("--task-id", dest="task_id", help="specific task id to reset")
    parser.add_argument("--all", action="store_true", help="scan project-wide for stuck rows")
    parser.add_argument("--apply", action="store_true", help="actually write changes (default is dry-run)")
    args = parser.parse_args()

    if not (args.session or args.task_id or args.all):
        parser.error("provide one of --session, --task-id, or --all")

    db_path = _project_db_path(args.project)
    if not db_path.exists():
        print(f"ERROR: project database not found: {db_path}", file=sys.stderr)
        return 3

    print(f"Project DB : {db_path}")
    print(f"Mode       : {'APPLY (writes)' if args.apply else 'DRY-RUN (read-only)'}")
    print()

    conn = sqlite3.connect(str(db_path))
    try:
        task_ids, session_ids = _resolve_targets(
            conn,
            task_id=args.task_id,
            session_id_or_title=args.session,
            include_all=args.all,
        )

        if not task_ids and not session_ids:
            print("No matching tasks or sessions found for the given filters.")
            return 0

        print(f"Matched tasks   : {len(task_ids)}")
        print(f"Matched sessions: {len(session_ids)}")

        findings = _scan_stuck(conn, task_ids, session_ids)
        if not findings:
            print("Nothing looks stuck for the matched targets.")
            return 0

        print(f"\nStuck rows ({len(findings)}):")
        for row in findings:
            print(row.render())

        if not args.apply:
            print("\nDry-run: re-run with --apply to reset these rows.")
            return 0

        counts = _apply_reset(conn, task_ids, session_ids)
        print("\nApplied:")
        print(f"  tasks reset (running -> pending)   : {counts['tasks_reset']}")
        print(f"  execution locks cleared             : {counts['locks_cleared']}")
        print(f"  runtime_sessions ended              : {counts['runtime_sessions_ended']}")

        print("\nNext steps (required for in-memory recovery):")
        print("  1. Restart the AutoSwarm office_ui server (it holds asyncio locks in memory).")
        print("  2. In the browser, hard-reload the tab (Ctrl+Shift+R) to drop stale client state.")
        print("     Optional: clear localStorage for the AutoSwarm origin if the board still looks stale.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
