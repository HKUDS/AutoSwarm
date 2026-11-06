"""Process events of AutoSwarm calls, read from each agent's own session records.

Claude Code, Codex and Cursor already keep every session on disk, so Desk
reads those records (read-only, only the exact native session of the call)
instead of keeping a second copy. Other agents keep using the optional capture
(events.jsonl). Where the records live depends on how the agent was installed:

- Claude Code: the CLI, the VS Code extension (which runs its bundled CLI) and
  the desktop app on a local folder all write $CLAUDE_CONFIG_DIR or
  ~/.claude/projects/<cwd with every non-alphanumeric as ->/<session>.jsonl.
  The desktop app's local agent mode gives each session a private config dir
  under its data folder (MSIX: %LOCALAPPDATA%/Packages/Claude_*/LocalCache/
  Local/Claude*/local-agent-mode-sessions/.../.claude/projects/*/<id>.jsonl).
- Codex: the desktop app, the CLI and the VS Code extension share $CODEX_HOME
  (~/.codex). Newer builds project every turn into thread_history_N.sqlite;
  all builds write the rollout JSONL (sessions/YYYY/MM/DD/rollout-*-<thread>.
  jsonl, later archived_sessions/), found through state_N.sqlite. Codex prunes
  old rollouts itself while the projection stays, so a finished call reads the
  projection first and a running call reads the rollout, which is written live.
- Cursor: the agent CLI (standalone, or the copy bundled with the Cursor IDE)
  keeps ACP sessions in $CURSOR_CONFIG_DIR or ~/.cursor/acp-sessions/<id>/
  store.db and terminal chats in ~/.cursor/chats/<hash>/<id>/store.db: a
  content-addressed blob store whose root blob lists the message blobs in
  order. Chats inside the Cursor editor (state.vscdb) are not AutoSwarm calls.

A native session spans several AutoSwarm calls (each resume is a new call), so
each call reads only its own turns: Claude and Codex by time, between the
call's start and the next call of the same session; Cursor, whose messages
carry no timestamps, by order (the n-th prompt belongs to the n-th call).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .lens import normalize, tool_kind
from .native_history import _process as codex_item

from ..capture import NATIVE_RECORDS as READABLE  # noqa: E402  Shared with capture "auto".
EVENT_LIMIT = 800
TEXT_LIMIT = 20000
OUTPUT_LIMIT = 12000
NAMES = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor"}


class Missing(Exception):
    """The agent's own record of this session is not (or no longer) on disk."""


def session_of(call):
    return (call.get("handle") or {}).get("native_session_id") or (call.get("task") or {}).get("native_session_id")


def _epoch(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _clip(value, limit):
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit] + f"\n…（已截断，共 {len(value)} 字）"


def _plain(path):
    return Path(str(path).removeprefix("\\\\?\\"))


def _database(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)
    connection.row_factory = sqlite3.Row
    return connection


# Appended JSONL is read incrementally; a few recently used files stay parsed.
_files = {}


def _jsonl(path):
    stat = path.stat()
    key, signature = str(path), (stat.st_size, stat.st_mtime_ns)
    cached = _files.pop(key, None)
    if cached and cached[0] == signature:
        _files[key] = cached
        return cached[2]
    offset, rows = (cached[1], list(cached[2])) if cached and stat.st_size >= cached[1] else (0, [])
    with path.open("rb") as stream:
        stream.seek(offset)
        chunk = stream.read()
    complete = chunk.rfind(b"\n") + 1  # An unfinished last line waits for the next read.
    for line in chunk[:complete].splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    _files[key] = (signature, offset + complete, rows)
    while len(_files) > 8:
        _files.pop(next(iter(_files)))
    return rows


# ---- locating -------------------------------------------------------------

def _roots(variable, default, extra=()):
    roots = [Path(os.environ[variable])] if os.environ.get(variable) else []
    roots += [Path(p) for p in extra]
    roots.append(default)
    unique = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


# Found records are remembered; a fresh session may not have written its file yet,
# so the cheap places are checked again on every scan and deep searches at most
# once a minute per session.
_located, _searched = {}, {}


def _remembered(key):
    path = _located.get(key)
    return path if path is not None and path.is_file() else None


def claude_transcript(session, workspace=""):
    key = ("claude-code", session)
    if found := _remembered(key):
        return found
    name = session + ".jsonl"
    for root in _roots("CLAUDE_CONFIG_DIR", Path.home() / ".claude"):
        projects = root / "projects"
        guess = projects / re.sub(r"[^A-Za-z0-9]", "-", str(workspace)) / name if workspace else None
        for found in ([guess] if guess and guess.is_file() else []) or projects.glob("*/" + name):
            _located[key] = found
            return found
    if time.monotonic() - _searched.get(key, -120) >= 60:
        _searched[key] = time.monotonic()
        local = os.environ.get("LOCALAPPDATA")
        bases = [*Path(local, "Packages").glob("Claude_*/LocalCache/Local"), Path(local)] if local else []
        for base in bases:
            for sessions in base.glob("Claude*/local-agent-mode-sessions"):
                for found in sessions.glob("**/.claude/projects/*/" + name):
                    _located[key] = found
                    return found
    raise Missing


def _codex_roots():
    return _roots("CODEX_HOME", Path.home() / ".codex")


def _latest(root, prefix):
    found = []
    for path in root.glob(prefix + "_*.sqlite"):
        try:
            found.append((int(path.stem.rsplit("_", 1)[-1]), path))
        except ValueError:
            continue
    return max(found)[1] if found else None


def codex_rollout(session):
    """The thread's rollout (moved to archived_sessions when archived), or None."""
    key = ("codex", session)
    if found := _remembered(key):
        return found
    for root in _codex_roots():
        state = _latest(root, "state")
        if state:
            try:
                with closing(_database(state)) as db:
                    row = db.execute("SELECT rollout_path FROM threads WHERE id=?", (session,)).fetchone()
                if row and row[0] and _plain(row[0]).is_file():
                    _located[key] = _plain(row[0])
                    return _located[key]
            except sqlite3.Error:
                pass
        for pattern in ("sessions/*/*/*/rollout-*-" + session + ".jsonl", "archived_sessions/rollout-*-" + session + ".jsonl"):
            for found in root.glob(pattern):
                _located[key] = found
                return found
    return None


def cursor_store(session):
    key = ("cursor", session)
    if found := _remembered(key):
        return found
    xdg = [Path(os.environ["XDG_CONFIG_HOME"]) / "cursor"] if os.environ.get("XDG_CONFIG_HOME") else []
    for root in _roots("CURSOR_CONFIG_DIR", Path.home() / ".cursor", xdg):
        direct = root / "acp-sessions" / session / "store.db"
        for found in ([direct] if direct.is_file() else []) or root.glob("chats/*/" + session + "/store.db"):
            _located[key] = found
            return found
    raise Missing


# ---- Claude Code ------------------------------------------------------------

def _add(total, input_tokens=0, cached=0, output=0):
    """Token use as Desk shows it: all input (cache included), cache hits, output."""
    for key, value in (("input", input_tokens), ("cached", cached), ("output", output)):
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + int(value)
    return total


def _claude(call, start, end):
    path = claude_transcript(session_of(call), call["task"].get("workspace", ""))
    events, usage, counted = [], {}, set()
    for row in _jsonl(path):
        if row.get("type") not in ("assistant", "user") or row.get("isSidechain") or row.get("isMeta") or row.get("isCompactSummary"):
            continue
        at = _epoch(row.get("timestamp"))
        if at is None or at < start - 2 or (end is not None and at >= end):
            continue
        message = row.get("message") or {}
        if row["type"] == "assistant" and isinstance(message.get("usage"), dict) and message.get("id") not in counted:
            counted.add(message.get("id"))  # One API message is split over several lines.
            u = message["usage"]
            _add(usage, (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0),
                 u.get("cache_read_input_tokens") or 0, u.get("output_tokens") or 0)
        content = message.get("content")
        # The prompt itself is the call's task; only tool results are kept from user lines.
        if row["type"] == "user" and not (isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)):
            continue
        # One assistant message is split over several lines (thinking, text, tool use),
        # each numbering its blocks from 0; the line's uuid keeps their ids apart.
        for number, item in enumerate(normalize("claude-code", row)):
            item.pop("raw", None)
            item["at"] = row.get("timestamp")
            if item.get("kind") in ("message", "thought") and row.get("uuid"):
                item["id"] = f"{row['uuid']}:{number}"
            events.append(item)
    return events, [path], usage


# ---- Codex --------------------------------------------------------------------

TURN_STATUS = {"completed": "completed", "failed": "failed", "interrupted": "cancelled", "cancelled": "cancelled"}


def _codex_history(session, start, end):
    for root in _codex_roots():
        history = _latest(root, "thread_history")
        if not history:
            continue
        with closing(_database(history)) as db:
            turns = [dict(r) for r in db.execute("SELECT turn_id, status, started_at FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal", (session,))]
            chosen = [t for t in turns if t["started_at"] is not None and t["started_at"] >= start - 5 and (end is None or t["started_at"] < end)]
            if not chosen:
                continue
            events = []
            for turn in chosen:
                for row in db.execute("SELECT item_json, created_at_ms FROM thread_items WHERE thread_id=? AND turn_id=? ORDER BY rollout_ordinal", (session, turn["turn_id"])):
                    item = json.loads(row["item_json"])
                    if item.get("type") != "userMessage" and (event := codex_item(item, row["created_at_ms"])):
                        events.append(_codex_final(event))
                if turn["status"] in TURN_STATUS:
                    events.append({"kind": "turn", "status": TURN_STATUS[turn["status"]]})
            rollout = codex_rollout(session)  # The projection keeps no token counts.
            return events, [history], _codex_usage(_jsonl(rollout), start, end) if rollout else {}
    return None


def _codex_usage(rows, start, end):
    usage = {}
    for row in rows:
        payload = row.get("payload") or {}
        at = _epoch(row.get("timestamp"))
        if payload.get("type") == "token_count" and at is not None and at >= start - 5 and (end is None or at < end):
            last = (payload.get("info") or {}).get("last_token_usage") or {}
            _add(usage, last.get("input_tokens") or 0, last.get("cached_input_tokens") or 0, last.get("output_tokens") or 0)
    return usage


def _codex_final(event):
    if event.get("kind") == "message":
        event["final"] = event.pop("phase", None) == "final_answer"
    return event


def _camel(name):
    head, *rest = str(name).split("_")
    return head[:1].lower() + head[1:] + "".join(part[:1].upper() + part[1:] for part in rest)


def _rollout_item(item):
    """A rollout `item_completed` (core shape) as the app-server item codex_item expects."""
    value = {_camel(k): v for k, v in item.items()}
    value["type"] = _camel(item.get("type", ""))
    content = item.get("content")
    if value["type"] == "agentMessage" and isinstance(content, list):
        value["text"] = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if value["type"] == "reasoning":
        value["summary"] = item.get("summary_text") or item.get("summary") or []
    if isinstance(item.get("command"), list):
        value["command"] = subprocess.list2cmdline([str(p) for p in item["command"]])
    if isinstance(item.get("parsed_cmd"), list):
        value["commandActions"] = [{**a, "command": a.get("cmd")} for a in item["parsed_cmd"] if isinstance(a, dict)]
    if isinstance(item.get("changes"), dict):
        value["changes"] = [{"path": p, "kind": (c or {}).get("type"), "diff": (c or {}).get("unified_diff") or (c or {}).get("content", "")} for p, c in item["changes"].items()]
    duration = item.get("duration")
    if isinstance(duration, dict):
        value["durationMs"] = int(duration.get("secs", 0) * 1000 + duration.get("nanos", 0) / 1e6)
    return value


def _response_item(payload):
    """Older rollouts carry only Responses API items; map the visible ones."""
    kind = payload.get("type")
    if kind == "message" and payload.get("role") == "assistant":
        text = "".join(p.get("text", "") for p in payload.get("content") or [] if isinstance(p, dict) and p.get("type") == "output_text")
        return {"kind": "message", "id": payload.get("id"), "text": text, "final": payload.get("phase") == "final_answer"} if text else None
    if kind == "reasoning":
        text = "\n\n".join(p.get("text", "") for p in payload.get("summary") or [] if isinstance(p, dict))
        return {"kind": "thought", "id": payload.get("id"), "text": text} if text.strip() else None
    if kind in ("function_call", "custom_tool_call", "local_shell_call"):
        raw = payload.get("arguments") or payload.get("input") or (payload.get("action") or {})
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            args = raw
        command = args.get("command") or args.get("cmd") if isinstance(args, dict) else None
        if isinstance(command, list):
            command = subprocess.list2cmdline([str(p) for p in command])
        title = command or payload.get("name") or "工具"
        return {"kind": "tool", "id": payload.get("call_id") or payload.get("id"), "tool": "execute" if command else tool_kind(payload.get("name")),
                "title": str(title)[:300], "status": "in_progress", "input": args if isinstance(args, dict) else None}
    if kind in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
        output = payload.get("output")
        if isinstance(output, str) and output.startswith("{"):  # Older builds: '{"output": ..., "metadata": ...}'.
            try:
                output = json.loads(output)
            except ValueError:
                pass
        if isinstance(output, dict):
            output = output.get("output") or output.get("content")
        if isinstance(output, list):
            output = "\n".join(p.get("text", "") for p in output if isinstance(p, dict))
        return {"kind": "tool", "id": payload.get("call_id"), "status": "completed", "output": output}
    if kind == "web_search_call":
        action = payload.get("action") or {}
        return {"kind": "tool", "id": payload.get("id"), "tool": "search", "title": action.get("query") or "网页搜索", "status": "completed"}
    return None


def _codex_rollout(path, start, end):
    rows = [r for r in _jsonl(path) if (at := _epoch(r.get("timestamp"))) is not None and at >= start - 5 and (end is None or at < end)]
    structured = any((r.get("payload") or {}).get("type") == "item_completed" for r in rows)
    events = []
    for row in rows:
        payload = row.get("payload") or {}
        kind = payload.get("type")
        if row.get("type") == "event_msg" and kind == "item_completed" and isinstance(payload.get("item"), dict):
            item = _rollout_item(payload["item"])
            if item["type"] != "userMessage" and (event := codex_item(item, payload.get("completed_at_ms"))):
                events.append(_codex_final(event))
        elif row.get("type") == "response_item" and not structured and (event := _response_item(payload)):
            events.append(event)
        elif row.get("type") == "event_msg" and kind in ("task_complete", "turn_aborted"):
            events.append({"kind": "turn", "status": "completed" if kind == "task_complete" else "cancelled"})
    return events, [path], _codex_usage(rows, start, end)


def _codex(call, start, end, running):
    session = session_of(call)
    if not running:
        found = _codex_history(session, start, end)
        if found:
            return found
    rollout = codex_rollout(session)
    if rollout:
        return _codex_rollout(rollout, start, end)
    found = _codex_history(session, start, end)
    if found:
        return found
    raise Missing


# ---- Cursor -------------------------------------------------------------------

def _varint(data, index):
    value = shift = 0
    while True:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, index
        shift += 7


def _message_ids(root):
    """Field 1 of the root blob (protobuf) repeats the 32-byte ids of the messages, in order."""
    ids, index = [], 0
    try:
        while index < len(root):
            key, index = _varint(root, index)
            field, wire = key >> 3, key & 7
            if wire == 0:
                _, index = _varint(root, index)
            elif wire == 1:
                index += 8
            elif wire == 5:
                index += 4
            elif wire == 2:
                size, index = _varint(root, index)
                if field == 1 and size == 32:
                    ids.append(root[index:index + size].hex())
                index += size
            else:
                break
    except IndexError:
        pass
    return ids


def _cursor_messages(path):
    with closing(_database(path)) as db:
        row = db.execute("SELECT value FROM meta WHERE key='0'").fetchone()
        meta = json.loads(bytes.fromhex(row[0]).decode("utf-8")) if row else {}
        root = db.execute("SELECT data FROM blobs WHERE id=?", (meta.get("latestRootBlobId"),)).fetchone()
        if not root:
            return []
        messages = []
        for blob_id in _message_ids(bytes(root[0])):
            found = db.execute("SELECT data FROM blobs WHERE id=?", (blob_id,)).fetchone()
            try:
                value = json.loads(bytes(found[0])) if found else None
            except ValueError:
                value = None
            if isinstance(value, dict) and value.get("role"):
                messages.append(value)
        return messages


def _cursor_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def _cursor_tool_title(name, args):
    args = args if isinstance(args, dict) else {}
    for key in ("command", "path", "target_file", "file_path", "pattern", "query", "url", "glob_pattern"):
        if isinstance(args.get(key), str) and args[key]:
            return args[key][:300]
    return name or "工具"


def _cursor(call, index, last, has_result):
    path = cursor_store(session_of(call))
    turns = []
    for message in _cursor_messages(path):
        if message.get("role") == "user" and "<user_query>" in _cursor_text(message):
            turns.append([])
        elif turns and message.get("role") in ("assistant", "tool"):
            turns[-1].append(message)
    # The n-th prompt is the n-th call's; prompts sent within the last call stay with it.
    mine = turns[index:] if last else turns[index:index + 1]
    events = []
    for turn in mine:
        for message in turn:
            for part in message.get("content") or []:
                if not isinstance(part, dict):
                    continue
                kind = part.get("type")
                if kind == "reasoning" and part.get("text", "").strip():
                    events.append({"kind": "thought", "text": part["text"]})
                elif kind == "text" and part.get("text"):
                    events.append({"kind": "message", "text": part["text"]})
                elif kind == "tool-call":
                    args = part.get("args") or part.get("input")
                    events.append({"kind": "tool", "id": part.get("toolCallId"), "tool": tool_kind(part.get("toolName")),
                                   "title": _cursor_tool_title(part.get("toolName"), args), "status": "in_progress", "input": args if isinstance(args, dict) else None})
                elif kind == "tool-result":
                    output = part.get("result") if part.get("result") is not None else part.get("output")
                    events.append({"kind": "tool", "id": part.get("toolCallId"), "status": "failed" if part.get("isError") else "completed",
                                   "output": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)})
        if has_result:
            events.append({"kind": "turn", "status": "completed"})
    return events, [path, Path(str(path) + "-wal")], {}  # Cursor's store keeps no token counts.


# ---- one call -------------------------------------------------------------------

def _siblings(call, calls):
    provider, session = call["task"].get("provider"), session_of(call)
    same = [c for c in calls.values() if c["task"].get("provider") == provider and session_of(c) == session]
    return sorted(same, key=lambda c: (c["task"].get("created_at", ""), c["id"]))


def load_calls(home):
    """{call_id: {"id", "task", "handle", "result"}} straight from the records."""
    calls = {}
    for directory in Path(home, "calls").glob("call_*"):
        def read(name):
            try:
                return json.loads((directory / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        task = read("task.json")
        if isinstance(task, dict):
            calls[directory.name] = {"id": directory.name, "task": task, "handle": read("handle.json") or {}, "result": read("result.json")}
    return calls


def applies(call):
    return (call.get("task") or {}).get("provider") in READABLE and bool(session_of(call))


def page(call_id, calls):
    """The call's process as a stream page (same shape as a captured page), or
    {"available": False, "missing": ...} when the agent no longer has it."""
    call = calls.get(call_id)
    if not call or not applies(call):
        return None
    provider = call["task"]["provider"]
    siblings = _siblings(call, calls)
    index = next(i for i, c in enumerate(siblings) if c["id"] == call_id)
    start = _epoch(call["task"].get("created_at")) or 0
    end = _epoch(siblings[index + 1]["task"].get("created_at")) if index + 1 < len(siblings) else None
    result = call.get("result")
    try:
        if provider == "claude-code":
            events, sources, usage = _claude(call, start, end)
        elif provider == "codex":
            events, sources, usage = _codex(call, start, end, result is None)
        else:
            events, sources, usage = _cursor(call, index, index + 1 == len(siblings), result is not None)
    except (Missing, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        # A running agent may not have written its record yet; only a finished call is "missing".
        return {"events": [], "before": 0, "size": 0, "available": False, "source": "native",
                **({"missing": f"未找到 {NAMES[provider]} 自己的会话记录（可能已被它清理）"} if result else {})}
    if result and not any(e.get("kind") == "turn" for e in events[-1:]):
        events.append({"kind": "turn", "status": "failed" if result.get("outcome") == "failed" else "completed"})
    events = events[-EVENT_LIMIT:]
    for number, event in enumerate(events):
        for key, limit in (("text", TEXT_LIMIT), ("output", OUTPUT_LIMIT)):
            event[key] = _clip(event.get(key), limit) if key in event else None
            if event[key] is None:
                event.pop(key)
        event["seq"] = f"{number}:0"
    return {"events": events, "before": 0, "end": len(events), "size": len(events), "available": True, "source": "native",
            "location": str(sources[0]), **({"usage": usage} if usage else {})}


def signature(call_id, calls):
    """Changes whenever the agent writes this call's own session record (a running
    Codex call writes its rollout live); cheap enough for every scan. A record
    that does not exist yet is looked for again every few seconds."""
    call = calls.get(call_id)
    if not call or not applies(call):
        return None
    provider, session = call["task"]["provider"], session_of(call)
    try:
        if provider == "claude-code":
            paths = [claude_transcript(session, call["task"].get("workspace", ""))]
        elif provider == "codex":
            rollout = codex_rollout(session)
            if not rollout:
                raise Missing
            paths = [rollout]
        else:
            store = cursor_store(session)
            paths = [store, Path(str(store) + "-wal")]
    except (Missing, OSError, sqlite3.Error):
        return ("missing", int(time.time() // 5))
    stamp = []
    for path in paths:
        try:
            stat = Path(path).stat()
            stamp.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue
    return tuple(stamp)
