"""Read the exact bound native conversation, without changing its database.

Hosts: Codex (its thread_history projection) and Claude Code (the session's
transcript JSONL, wherever the CLI, VS Code extension or desktop app kept it).
Codex's local projection is optional. Unknown schema versions simply leave the
normal AutoSwarm conversation available. No unbound chats are imported.

Turns mirror Codex's own structure: the user's message, the process items of
the turn (commentary, reasoning summaries, commands, searches, tool calls, file
changes, plans) mapped onto the lens vocabulary, and the turn's final answer.
Large payloads are clipped and the item count is budgeted so snapshot frames
stay small; the full history remains in Codex.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

TURN_LIMIT = 12
ITEM_BUDGET = 400
OUTPUT_LIMIT = 900
DIFF_LIMIT = 2400
TEXT_LIMIT = 6000
ANSWER_LIMIT = 60000
ENVELOPES = ("AutoSwarm child result", "AutoSwarm agent message")


def _database(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _clip(value, limit, tail=False):
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if len(text) <= limit:
        return text
    return "…" + text[-limit:] if tail else text[:limit] + "…"


def _columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _user_text(item):
    return "\n".join(part.get("text", "") for part in item.get("content", []) if isinstance(part, dict) and part.get("type") == "text")


def _status(value):
    return {"inProgress": "in_progress", "completed": "completed", "failed": "failed",
            "declined": "declined", "interrupted": "cancelled"}.get(value, value)


def _process(item, at):
    """Map one Codex thread item onto the unified event vocabulary (kind/tool/title/status)."""
    kind = item.get("type")
    base = {"id": item.get("id"), "at": at}
    if kind == "agentMessage":
        # Clipped in _finish, after the final answer has been chosen.
        return {**base, "kind": "message", "text": item.get("text", ""), "phase": item.get("phase")}
    if kind == "reasoning":
        parts = [p if isinstance(p, str) else (p or {}).get("text", "") for p in item.get("summary") or []]
        text = "\n\n".join(p for p in parts if p)
        return {**base, "kind": "thought", "text": _clip(text, TEXT_LIMIT)} if text.strip() else None
    if kind == "commandExecution":
        actions = item.get("commandActions") or []
        action = actions[0] if len(actions) == 1 and isinstance(actions[0], dict) else {}
        tool = {"read": "read", "search": "search", "listFiles": "search"}.get(action.get("type"), "execute")
        title = (f"读取 {action.get('path') or action.get('name')}" if tool == "read" and (action.get("path") or action.get("name"))
                 else action.get("command") or item.get("command", ""))
        exit_code = item.get("exitCode")
        return {**base, "kind": "tool", "tool": tool, "title": _clip(title, 300), "status": _status(item.get("status")),
                "output": _clip(item.get("aggregatedOutput") or "", OUTPUT_LIMIT, tail=True) or None,
                "exit": exit_code, "duration": item.get("durationMs")}
    if kind == "webSearch":
        action = item.get("action") or {}
        url = action.get("url") if isinstance(action, dict) else None
        return {**base, "kind": "tool", "tool": "fetch" if url and not item.get("query") else "search",
                "title": _clip(item.get("query") or url or "网页搜索", 300), "status": "completed",
                "output": "\n".join(r.get("url", "") for r in (item.get("results") or [])[:8] if isinstance(r, dict)) or None}
    if kind in ("mcpToolCall", "dynamicToolCall"):
        source = item.get("server") or item.get("namespace") or "tool"
        content = (item.get("result") or {}).get("content") if kind == "mcpToolCall" else item.get("contentItems")
        text = "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
        error = item.get("error")
        return {**base, "kind": "tool", "tool": "other", "title": f"{source} · {item.get('tool', '')}",
                "status": _status(item.get("status")) if not error else "failed",
                "output": _clip(error if isinstance(error, str) and error else text, OUTPUT_LIMIT) or None, "duration": item.get("durationMs")}
    if kind == "fileChange":
        changes = [{"path": c.get("path", ""), "type": (c.get("kind") or {}).get("type") if isinstance(c.get("kind"), dict) else c.get("kind"),
                    "diff": _clip(c.get("diff", ""), DIFF_LIMIT)} for c in item.get("changes") or [] if isinstance(c, dict)]
        names = ", ".join(Path(c["path"]).name for c in changes[:3]) + (f" 等 {len(changes)} 个文件" if len(changes) > 3 else "")
        return {**base, "kind": "tool", "tool": "edit", "title": f"修改 {names}" if names else "修改文件",
                "status": _status(item.get("status")), "changes": changes}
    if kind == "imageView":
        return {**base, "kind": "tool", "tool": "read", "title": f"查看图片 {Path(item.get('path', '')).name}", "status": "completed"}
    if kind == "plan":
        return {**base, "kind": "plan", "title": "更新计划", "text": _clip(item.get("text", ""), TEXT_LIMIT)}
    if kind == "subAgentActivity":
        return {**base, "kind": "tool", "tool": "other", "title": f"子 agent · {item.get('agentPath') or item.get('kind') or ''}".strip(), "status": "completed"}
    if kind == "contextCompaction":
        return {**base, "kind": "notice", "title": "上下文已压缩"}
    if kind == "imageGeneration":
        return {**base, "kind": "tool", "tool": "other", "title": "生成图片", "status": "completed"}
    return {**base, "kind": "notice", "title": kind or "未知条目"}


def _finish(turn):
    """Pick the final answer: Codex's own pointer, else the last final_answer, else the last message of an ended turn."""
    final_id = turn.pop("_final", None)
    messages = [i for i in turn["items"] if i["kind"] == "message"]
    final = next((m for m in messages if final_id and final_id in (m.get("item_id"), m["id"])), None)
    final = final or next((m for m in reversed(messages) if m.get("phase") == "final_answer"), None)
    if not final and turn["status"] != "in_progress" and messages:
        final = messages[-1]
    if final:
        turn["items"].remove(final)
        turn["final"] = {"id": final["id"], "text": _clip(final.get("text", ""), ANSWER_LIMIT), "at": final["at"]}
    for item in turn["items"]:
        item.pop("item_id", None)
        if item["kind"] == "message":
            item["text"] = _clip(item.get("text", ""), TEXT_LIMIT)
    return turn


def _turns_from_items(db, sid):
    rows = db.execute("SELECT turn_id,status,started_at,completed_at,duration_ms,final_agent_item_id,error_json FROM thread_turns "
                      "WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT ?", (sid, TURN_LIMIT + 1)).fetchall()
    has_more = len(rows) > TURN_LIMIT
    rows = list(reversed(rows[:TURN_LIMIT]))
    if not rows:
        return [], False
    turns = {}
    for row in rows:
        error = None
        if row["error_json"]:
            try:
                value = json.loads(row["error_json"])
                error = value.get("message") if isinstance(value, dict) else str(value)
            except ValueError:
                error = row["error_json"]
        started = row["started_at"]
        turns[row["turn_id"]] = {"id": row["turn_id"], "status": _status(row["status"]), "items": [], "user": None, "final": None,
                                 "started_at": started * 1000 if isinstance(started, (int, float)) else None,
                                 "duration_ms": row["duration_ms"], "error": _clip(error, 2000) if error else None,
                                 "_final": row["final_agent_item_id"]}
    marks = ",".join("?" * len(turns))
    items = db.execute(f"SELECT turn_id,item_id,item_json,item_type,created_at_ms FROM thread_items WHERE thread_id=? AND turn_id IN ({marks}) "
                       "ORDER BY rollout_ordinal", (sid, *turns)).fetchall()
    for row in items:
        turn = turns.get(row["turn_id"])
        try:
            item = json.loads(row["item_json"])
        except ValueError:
            continue
        item.setdefault("id", row["item_id"])
        if row["item_type"] == "userMessage":
            text = _user_text(item)
            if text.startswith("<environment_context>") or not text.strip():
                continue
            if text.startswith(ENVELOPES):
                turn["items"].append({"id": item["id"], "at": row["created_at_ms"], "kind": "notice", "title": "收到 AutoSwarm 回传",
                                      "text": _clip(text, TEXT_LIMIT)})
            elif turn["user"] is None:
                turn["user"] = {"id": item["id"], "text": _clip(text, TEXT_LIMIT), "at": row["created_at_ms"]}
            else:
                turn["items"].append({"id": item["id"], "at": row["created_at_ms"], "kind": "user", "text": _clip(text, TEXT_LIMIT)})
            continue
        mapped = _process(item, row["created_at_ms"])
        if mapped:
            mapped["item_id"] = row["item_id"]
            turn["items"].append(mapped)
    return _budget([_finish(turn) for turn in turns.values()]), has_more


def _budget(ordered):
    """Budget the newest turns first; older turns keep their latest items."""
    budget = ITEM_BUDGET
    for turn in reversed(ordered):
        keep = max(0, min(len(turn["items"]), budget))
        turn["omitted"] = len(turn["items"]) - keep
        turn["items"] = turn["items"][len(turn["items"]) - keep:] if keep else []
        budget -= keep
    return ordered


def _turns_from_messages(db, sid, limit):
    """Older projections have no turn ids: a user message starts a turn."""
    rows = db.execute("SELECT item_json,created_at_ms FROM thread_items WHERE thread_id=? AND item_type IN ('userMessage','agentMessage') "
                      "ORDER BY rollout_ordinal DESC LIMIT ?", (sid, limit)).fetchall()
    turns = []
    for row in reversed(rows):
        item = json.loads(row["item_json"])
        if item.get("type") == "userMessage":
            text = _user_text(item)
            if text.startswith(("<environment_context>",) + ENVELOPES) or not text.strip():
                continue
            turns.append({"id": str(len(turns)), "status": "completed", "user": {"id": item.get("id"), "text": text, "at": row["created_at_ms"]},
                          "items": [], "final": None, "started_at": row["created_at_ms"], "duration_ms": None, "error": None})
        elif (item.get("text") or "").strip():
            if not turns:
                turns.append({"id": "0", "status": "completed", "user": None, "items": [], "final": None,
                              "started_at": row["created_at_ms"], "duration_ms": None, "error": None})
            turns[-1]["items"].append({"id": item.get("id"), "at": row["created_at_ms"], "kind": "message", "text": item.get("text", ""), "phase": item.get("phase")})
    return [_finish(turn) for turn in turns], len(rows) == limit


def _ms(value):
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _claude_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return None


def _claude_output(content):
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


ENDED = ("end_turn", "stop_sequence", "max_tokens", "refusal")
STALE_MS = 15 * 60 * 1000  # A turn with no new line for this long is not "running" (the terminal was closed).


def _claude_turns(rows):
    """Claude Code's transcript as turns: each typed prompt starts one; assistant
    lines add thinking, text and tool uses; tool results fill their tool; the
    turn ends with turn_duration or an end_turn stop reason."""
    from .lens import normalize
    turns, tools = [], {}

    def turn_for(at, user=None, uid=None):
        turns.append({"id": uid or str(len(turns)), "status": "in_progress", "items": [], "user": user, "final": None,
                      "started_at": at, "duration_ms": None, "error": None, "_end": None})
        return turns[-1]

    for row in rows:
        kind = row.get("type")
        if row.get("isSidechain") or kind not in ("user", "assistant", "system"):
            continue
        at = _ms(row.get("timestamp"))
        current = turns[-1] if turns else None
        if kind == "system":
            if row.get("subtype") == "turn_duration" and current:
                current["status"] = "completed" if current["status"] == "in_progress" else current["status"]
                current["duration_ms"] = row.get("durationMs") or current["duration_ms"]
                current["_end"] = at
            elif row.get("subtype") == "compact_boundary":
                (current or turn_for(at))["items"].append({"id": row.get("uuid"), "at": at, "kind": "notice", "title": "上下文已压缩"})
            continue
        message = row.get("message") or {}
        if kind == "user":
            if row.get("isMeta") or row.get("isCompactSummary"):
                continue
            text = _claude_text(message.get("content"))
            if text is not None:
                if text.startswith(("<command-", "<local-command")) or not text.strip():
                    continue
                if text.startswith("[Request interrupted"):
                    if current:
                        current["status"], current["_end"] = "cancelled", at
                    continue
                turn_for(at, {"id": row.get("uuid"), "text": _clip(text, TEXT_LIMIT), "at": at}, row.get("uuid"))
                continue
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in tools:
                    tool = tools[block["tool_use_id"]]
                    tool["status"] = "failed" if block.get("is_error") else "completed"
                    tool["output"] = _clip(_claude_output(block.get("content")), OUTPUT_LIMIT, tail=True)
            continue
        current = current or turn_for(at)
        for number, event in enumerate(normalize("claude-code", row)):
            event.pop("raw", None)
            event["at"] = at
            if event.get("kind") == "tool":
                event.pop("input", None)
                event["title"] = _clip(str(event.get("title") or ""), 300)
                tools[event.get("id")] = event
            elif event.get("kind") in ("message", "thought"):
                if not (event.get("text") or "").strip():
                    continue
                event["id"] = f"{row.get('uuid')}:{number}"
                event.pop("replace", None)
            current["items"].append(event)
        if message.get("stop_reason") in ENDED:
            current["status"] = "completed" if current["status"] == "in_progress" else current["status"]
            current["_end"] = at
    for index, turn in enumerate(turns):
        end = turn.pop("_end")
        if turn["status"] == "in_progress" and index < len(turns) - 1:
            turn["status"] = "completed"  # A later prompt started, so this one is over.
        if turn["duration_ms"] is None and end and turn["started_at"]:
            turn["duration_ms"] = end - turn["started_at"]
    return [_finish(turn) for turn in turns]


_claude_cache = {}


def _claude_conversation(binding, detail):
    from .native_calls import Missing, _jsonl, claude_transcript
    sid = binding["session_id"]
    try:
        path = claude_transcript(sid, binding.get("workspace") or "")
        stat = path.stat()
        rows = _jsonl(path)
    except (Missing, OSError):
        return {}
    signature = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _claude_cache.get(sid)
    if not cached or cached[0] != signature:
        cached = _claude_cache[sid] = (signature, _claude_turns(rows))
    turns = cached[1]
    # No new line for a long time: the terminal was closed mid-turn, nothing is running.
    running = bool(turns) and turns[-1]["status"] == "in_progress" and time.time() * 1000 - stat.st_mtime_ns / 1e6 < STALE_MS
    first = next((t["user"]["text"] for t in turns if t["user"]), "")
    workspace = binding.get("workspace") or next((r.get("cwd") for r in rows if r.get("cwd")), "")
    result = {"title": first.strip().splitlines()[0][:200] if first.strip() else "", "workspace": workspace, "session_id": sid,
              "provider": "claude-code", "busy": running,
              "version": f"{stat.st_size}:{stat.st_mtime_ns}"}
    if not detail:
        return result
    recent = [json.loads(json.dumps(t)) for t in turns[-TURN_LIMIT:]]  # The cache stays untouched by budgeting.
    if recent and recent[-1]["status"] == "in_progress" and not running:
        recent[-1]["status"] = "completed"
    result["turns"], result["has_more"], result["messages"] = _budget(recent), len(turns) > TURN_LIMIT, []
    for turn in result["turns"]:
        if turn["user"]:
            result["messages"].append({**turn["user"], "role": "user", "phase": None})
        if turn["final"]:
            result["messages"].append({**turn["final"], "role": "assistant", "phase": "final_answer"})
    return result


def conversation(binding, *, home=None, limit=80, detail=True):
    """Summary mode (detail=False) is what every snapshot carries: identity, busy
    state and a version stamp. The renderer reads full turns on demand when the
    stamp changes, so snapshots stay small. Codex and Claude Code hosts are read."""
    if binding.get("host") == "claude-code" and binding.get("session_id"):
        return _claude_conversation(binding, detail)
    if binding.get("host") != "codex" or not binding.get("session_id"):
        return {}
    root = Path(home or os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    sid = binding["session_id"]
    try:
        databases = sorted(root.glob("state_*.sqlite"), key=lambda p: int(p.stem.split("_")[-1]), reverse=True)
        if not databases:
            return {}
        with closing(_database(databases[0])) as db:
            record = db.execute("SELECT * FROM threads WHERE id=?", (sid,)).fetchone()
            if not record:
                return {}
            record = dict(record)
        result = {"title": record.get("name") or record.get("title", ""), "workspace": record.get("cwd", ""),
                  "session_id": sid, "provider": "codex", "busy": None, "version": ""}
        histories = sorted(root.glob("thread_history_*.sqlite"), reverse=True)
        if not histories:
            return result
        with closing(_database(histories[0])) as db:
            latest = db.execute("SELECT status FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1", (sid,)).fetchone()
            result["busy"] = latest[0] == "inProgress" if latest else None
            columns = _columns(db, "thread_items")
            stamp = db.execute("SELECT max(rollout_ordinal)" + (", max(updated_at_ordinal)" if "updated_at_ordinal" in columns else ", 0") +
                               " FROM thread_items WHERE thread_id=?", (sid,)).fetchone()
            result["version"] = f"{stamp[0]}:{stamp[1]}:{latest[0] if latest else ''}"
            if not detail:
                return result
            structured = {"turn_id", "item_id"} <= columns and {"turn_id", "final_agent_item_id", "duration_ms"} <= _columns(db, "thread_turns")
            turns, more = _turns_from_items(db, sid) if structured else _turns_from_messages(db, sid, limit)
        result["turns"], result["has_more"], result["messages"] = turns, more, []
        # Flat user/final messages stay available for consumers that predate turns.
        for turn in turns:
            if turn["user"]:
                result["messages"].append({**turn["user"], "role": "user", "phase": None})
            if turn["final"]:
                result["messages"].append({**turn["final"], "role": "assistant", "phase": "final_answer"})
        return result
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return {}


def bound_conversations(agents):
    return {aid: value for aid, agent in agents.items() if (binding := agent.get("parent"))
            and (value := conversation(binding, detail=False))}


def agent_conversation(home, agent_id):
    """Full turns for one bound agent, read on demand by the desktop."""
    path = Path(home) / "agents" / agent_id / "agent.json"
    try:
        agent = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    binding = agent.get("parent") if isinstance(agent, dict) else None
    return conversation(binding, detail=True) if isinstance(binding, dict) else {}
