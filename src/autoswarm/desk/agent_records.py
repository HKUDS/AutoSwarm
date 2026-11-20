"""Process events of the other agents' calls, read from each agent's own session records.

Every agent AutoSwarm starts keeps its sessions on disk; Desk reads the exact native session of a call
(read-only) instead of saving a second copy. Where each agent keeps them (its home directory variable
first, then its default):

- CodeBuddy: $CODEBUDDY_CONFIG_DIR or ~/.codebuddy, projects/<cwd>/<session>.jsonl (millisecond times).
- Copilot: $COPILOT_HOME or ~/.copilot, session-state/<session>/events.jsonl (ISO times).
- Kimi Code: $KIMI_CODE_HOME or ~/.kimi-code, sessions/<workdir>/<session>/agents/main/wire.jsonl.
- Grok Build: $GROK_HOME or ~/.grok, sessions/<cwd>/<session>/updates.jsonl: the ACP updates it sent.
- OpenCode 2: $XDG_DATA_HOME or ~/.local/share, opencode/opencode.db (session_message rows).
- Hermes: $HERMES_HOME, else %LOCALAPPDATA%\\hermes on Windows and ~/.hermes elsewhere, state.db; the
  ACP session ID is the start of Hermes' own.
- DeepSeek Harness: $DSH_HOME or ~/.dsh, sessions/<cwd>/<session>/session*.jsonl[.zstd]: Zstandard
  frames by default, read with Python 3.14's compression.zstd (without it Desk keeps a copy instead).
- Nanobot: ~/.nanobot/sessions/<hash>/sessions.sqlite3 (messages of the session key, in order).
- Muse: $XDG_DATA_HOME, ~/.local/share, %LOCALAPPDATA% or %APPDATA%, or the private data folder a launcher
  gives it under the AutoSwarm home (native-profiles/*/data), muse/sessions/<Y>/<M>/<D>/<session>/session.jsonl.

These are each agent's own formats, not published interfaces: anything unknown is skipped, and a record
Desk cannot find or read shows no process instead of failing the conversation. A session spans several
calls (each resume is one); records with times are split by time, Nanobot's by its n-th user message.
"""
from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path

from .lens import normalize, tool_kind
from .native_calls import Missing, _add, _database, _epoch, _jsonl, _located, _remembered, _roots, session_of

NAMES = {"codebuddy": "CodeBuddy", "copilot": "Copilot", "kimi": "Kimi Code", "grok": "Grok Build", "opencode": "OpenCode",
         "hermes": "Hermes", "dsh": "DeepSeek Harness", "nanobot": "Nanobot", "muse": "Muse"}


def readable(provider, transport=None):
    """Agents whose records this module reads: OpenCode 2 only, DSH only with zstd (see capture.reads_native)."""
    from ..capture import reads_native
    return provider in NAMES and reads_native(provider, transport)


# ---- shared shapes ------------------------------------------------------------------

def _args(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {"input": value}
    return value if isinstance(value, dict) else {}


def _title(name, args):
    for key in ("command", "cmd", "file_path", "filePath", "path", "target_file", "pattern", "query", "url", "glob_pattern"):
        if isinstance(args.get(key), str) and args[key]:
            return args[key][:300]
    return name or "工具"


def _call(identity, name, args, status="in_progress"):
    args = _args(args)
    command = isinstance(args.get("command") or args.get("cmd"), str)
    return {"kind": "tool", "id": identity, "tool": "execute" if command else tool_kind(name), "title": _title(name, args),
            "status": status, "input": args or None}


def _text(value):
    """Tool output given as text, {"text"}, {"output"}, or a list of content parts."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "output", "content", "error"):
            if isinstance(value.get(key), (str, list)):
                return _text(value[key])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "\n".join(filter(None, (_text(part) for part in value)))
    return "" if value is None else str(value)


def _result(identity, output, failed=False):
    return {"kind": "tool", "id": identity, "status": "failed" if failed else "completed", "output": _text(output)}


def _within(at, window):
    """In this call's part of the session: from its start (a little earlier, unless the previous call had
    not ended by then) until the next call of the session starts."""
    return at is not None and at >= window["start"] and (window["end"] is None or at < window["end"])


def _first(key, candidates):
    if found := _remembered(key):
        return found
    for path in candidates():
        if path.is_file():
            _located[key] = path
            return path
    raise Missing


def _glob(roots, pattern):
    for root in roots:
        yield from root.glob(pattern)


# ---- CodeBuddy ------------------------------------------------------------------------

def _codebuddy_files(call):
    session = session_of(call)
    return [_first(("codebuddy", session), lambda: _glob([r / "projects" for r in _roots("CODEBUDDY_CONFIG_DIR", Path.home() / ".codebuddy")], f"*/{session}.jsonl"))]


def _codebuddy(call, files, window):
    events, usage, counted = [], {}, set()
    for row in _jsonl(files[0]):
        at = row["timestamp"] / 1000 if isinstance(row.get("timestamp"), (int, float)) else _epoch(row.get("timestamp"))
        if not _within(at, window):
            continue
        kind, data = row.get("type"), row.get("providerData") or {}
        spent = (row.get("message") or {}).get("usage")
        if isinstance(spent, dict) and data.get("messageId") not in counted:
            counted.add(data.get("messageId"))  # One model response is spread over several rows.
            _add(usage, spent.get("input_tokens") or 0, spent.get("cache_read_input_tokens") or 0, spent.get("output_tokens") or 0)
        if kind == "message" and row.get("role") == "assistant":
            text = "".join(p.get("text", "") for p in row.get("content") or [] if isinstance(p, dict))
            if text.strip():
                events.append({"kind": "message", "id": row.get("id"), "text": text})
        elif kind == "reasoning":
            parts = row.get("rawContent") or row.get("content") or row.get("summary") or []
            text = "\n\n".join(p.get("text", "") for p in parts if isinstance(p, dict))
            if text.strip():
                events.append({"kind": "thought", "id": row.get("id"), "text": text})
        elif kind == "function_call":
            events.append(_call(row.get("callId"), row.get("name"), row.get("arguments")))
        elif kind == "function_call_result":
            events.append(_result(row.get("callId"), row.get("output"), row.get("status") not in (None, "completed")))
    return events, usage


# ---- Copilot ------------------------------------------------------------------------------

def _copilot_files(call):
    session = session_of(call)
    return [_first(("copilot", session), lambda: (r / "session-state" / session / "events.jsonl" for r in _roots("COPILOT_HOME", Path.home() / ".copilot")))]


def _copilot(call, files, window):
    events, usage = [], {}
    for row in _jsonl(files[0]):
        if not _within(_epoch(row.get("timestamp")), window):
            continue
        kind, data = row.get("type"), row.get("data") or {}
        if kind == "assistant.message":
            if str(data.get("reasoningText") or "").strip():
                events.append({"kind": "thought", "id": f"{data.get('messageId')}:thought", "text": data["reasoningText"]})
            if str(data.get("content") or "").strip():
                events.append({"kind": "message", "id": data.get("messageId"), "text": data["content"]})
        elif kind == "tool.execution_start":
            event = _call(data.get("toolCallId"), data.get("toolName"), data.get("arguments"))
            if event["title"] == (data.get("toolName") or "工具") and data.get("toolTitle"):
                event["title"] = data["toolTitle"]
            events.append(event)
        elif kind == "tool.execution_complete":
            result = data.get("result")
            output = result.get("content") if isinstance(result, dict) and result.get("content") is not None else data.get("error") or result
            events.append(_result(data.get("toolCallId"), output, data.get("success") is False))
        elif kind == "session.usage_record":
            spent = data.get("usage") or {}
            _add(usage, spent.get("inputTokens") or 0, spent.get("cacheReadTokens") or 0, spent.get("outputTokens") or 0)
    return events, usage


# ---- Kimi Code ----------------------------------------------------------------------------

def _kimi_files(call):
    session = session_of(call)
    return [_first(("kimi", session), lambda: _glob([r / "sessions" for r in _roots("KIMI_CODE_HOME", Path.home() / ".kimi-code")], f"*/{session}/agents/main/wire.jsonl"))]


KIMI_TURN = {"completed": "completed", "cancelled": "cancelled", "interrupted": "cancelled", "failed": "failed", "error": "failed"}


def _kimi(call, files, window):
    events, usage = [], {}
    for row in _jsonl(files[0]):
        at = row["time"] / 1000 if isinstance(row.get("time"), (int, float)) else None
        if not _within(at, window):
            continue
        if row.get("type") == "turn.ended" and row.get("agentId", "main") == "main":
            events.append({"kind": "turn", "status": KIMI_TURN.get(row.get("reason"), "completed")})
            continue
        if row.get("type") != "context.append_loop_event" or row.get("agentId", "main") != "main":
            continue
        event = row.get("event") or {}
        kind = event.get("type")
        if kind == "content.part":
            part = event.get("part") or {}
            if part.get("type") == "think" and str(part.get("think") or "").strip():
                events.append({"kind": "thought", "id": event.get("uuid"), "text": part["think"]})
            elif part.get("type") == "text" and str(part.get("text") or "").strip():
                events.append({"kind": "message", "id": event.get("uuid"), "text": part["text"]})
        elif kind == "tool.call":
            item = _call(event.get("toolCallId"), event.get("name"), event.get("args"))
            display = event.get("display") or {}
            if isinstance(display.get("command"), str):
                item["title"], item["tool"] = display["command"][:300], "execute"
            events.append(item)
        elif kind == "tool.result":
            result = event.get("result") or {}
            events.append(_result(event.get("toolCallId"), result.get("output") if "output" in result else result.get("error") or result,
                                  bool(result.get("isError") or result.get("error"))))
        elif kind == "step.end":
            spent = event.get("usage") or {}
            cached = spent.get("inputCacheRead") or 0
            _add(usage, (spent.get("inputOther") or 0) + cached + (spent.get("inputCacheCreation") or 0), cached, spent.get("output") or 0)
    return events, usage


# ---- Grok Build -----------------------------------------------------------------------

def _grok_files(call):
    session = session_of(call)
    return [_first(("grok", session), lambda: _glob([r / "sessions" for r in _roots("GROK_HOME", Path.home() / ".grok")], f"*/{session}/updates.jsonl"))]


GROK_TURN = {"end_turn": "completed", "cancelled": "cancelled", "max_tokens": "completed", "max_turn_requests": "completed"}


def _grok(call, files, window):
    """Grok keeps the ACP updates it sent; Desk reads them as it reads a live ACP stream."""
    events, usage = [], {}
    for row in _jsonl(files[0]):
        at = row["timestamp_us"] / 1e6 if isinstance(row.get("timestamp_us"), (int, float)) else row.get("timestamp")
        if not _within(at, window):
            continue
        update = (row.get("params") or {}).get("update") or {}
        if row.get("method") == "session/update":
            for item in normalize("grok", {"method": "session/update", "params": row.get("params")}):
                item.pop("raw", None)
                if item.get("kind") not in ("user", "notice"):  # The prompt is the call's task.
                    events.append(item)
        elif update.get("sessionUpdate") == "turn_completed":
            spent = update.get("usage") or {}
            if spent:
                _add(usage, spent.get("inputTokens") or 0, spent.get("cachedReadTokens") or 0, spent.get("outputTokens") or 0)
            events.append({"kind": "turn", "status": GROK_TURN.get(update.get("stop_reason"), "failed")})
    return events, usage


# ---- OpenCode 2 ---------------------------------------------------------------------------

def _opencode_files(call):
    session = session_of(call)
    data = Path(os.environ["XDG_DATA_HOME"]) if os.environ.get("XDG_DATA_HOME") else Path.home() / ".local" / "share"
    path = data / "opencode" / "opencode.db"
    if not path.is_file():
        raise Missing
    with closing(_database(path)) as db:
        if not db.execute("SELECT 1 FROM session_v2 WHERE id=?", (session,)).fetchone():
            raise Missing
    return [path, Path(str(path) + "-wal")]


OPENCODE_STATUS = {"completed": "completed", "error": "failed", "running": "in_progress", "pending": "in_progress"}


def _opencode(call, files, window):
    events, usage = [], {}
    with closing(_database(files[0])) as db:
        rows = db.execute("SELECT id, type, time_created, data FROM session_message WHERE session_id=? ORDER BY seq", (session_of(call),)).fetchall()
    for row in rows:
        if not _within(row["time_created"] / 1000, window):
            continue
        data = json.loads(row["data"])
        if row["type"] == "idle":
            events.append({"kind": "turn", "status": "completed" if data.get("outcome") == "succeeded" else "failed"})
            continue
        if row["type"] != "assistant":
            continue
        for number, part in enumerate(data.get("content") or []):
            kind = part.get("type")
            if kind == "reasoning" and str(part.get("text") or "").strip():
                events.append({"kind": "thought", "id": f"{row['id']}:{number}", "text": part["text"]})
            elif kind == "text" and str(part.get("text") or "").strip():
                events.append({"kind": "message", "id": f"{row['id']}:{number}", "text": part["text"]})
            elif kind == "tool":
                state = part.get("state") or {}
                events.append(_call(part.get("id"), part.get("name"), state.get("input")))
                status = OPENCODE_STATUS.get(state.get("status"), "completed")
                if status != "in_progress":
                    events.append(_result(part.get("id"), state.get("content") or state.get("output") or state.get("error"), status == "failed"))
        tokens = data.get("tokens") or {}
        cache = tokens.get("cache") or {}
        _add(usage, (tokens.get("input") or 0) + (cache.get("read") or 0) + (cache.get("write") or 0), cache.get("read") or 0, tokens.get("output") or 0)
    return events, usage


# ---- Hermes -------------------------------------------------------------------------------

def _hermes_home():
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "hermes"
    return Path.home() / ".hermes"


def _hermes_session(path, session):
    with closing(_database(path)) as db:
        row = db.execute("SELECT id FROM sessions WHERE id=? OR id LIKE ? ORDER BY length(id) LIMIT 1", (session, session + "%")).fetchone()
    if not row:
        raise Missing
    return row[0]


def _hermes_files(call):
    path = _hermes_home() / "state.db"
    if not path.is_file():
        raise Missing
    _hermes_session(path, session_of(call))
    return [path, Path(str(path) + "-wal")]


def _hermes(call, files, window):
    events = []
    with closing(_database(files[0])) as db:
        session = db.execute("SELECT id FROM sessions WHERE id=? OR id LIKE ? ORDER BY length(id) LIMIT 1", (session_of(call), session_of(call) + "%")).fetchone()[0]
        rows = db.execute("SELECT id, role, content, tool_call_id, tool_calls, tool_name, timestamp, reasoning_content FROM messages WHERE session_id=? ORDER BY id", (session,)).fetchall()
    for row in rows:
        if not _within(row["timestamp"], window):
            continue
        if row["role"] == "assistant":
            if str(row["reasoning_content"] or "").strip():
                events.append({"kind": "thought", "id": f"{row['id']}:thought", "text": row["reasoning_content"]})
            if str(row["content"] or "").strip():
                events.append({"kind": "message", "id": str(row["id"]), "text": row["content"]})
            for tool in json.loads(row["tool_calls"] or "[]"):
                function = tool.get("function") or {}
                events.append(_call(tool.get("call_id") or tool.get("id"), function.get("name"), function.get("arguments")))
        elif row["role"] == "tool":
            output = row["content"]
            try:
                value = json.loads(output)
            except (TypeError, ValueError):
                value = None
            failed = isinstance(value, dict) and bool(value.get("error"))
            events.append(_result(row["tool_call_id"], value if isinstance(value, dict) else output, failed))
    return events, {}  # Hermes counts tokens per session, not per call.


# ---- DeepSeek Harness -----------------------------------------------------------------

def _dsh_files(call):
    session = session_of(call)

    def candidates():
        for folder in _glob([r / "sessions" for r in _roots("DSH_HOME", Path.home() / ".dsh")], f"*/{session}"):
            # The highest format generation is the current log (session.jsonl.zstd, session.v1…, session.v4…).
            logs = sorted(folder.glob("session*.jsonl*"), key=lambda p: (int(p.name.split(".")[1][1:]) if p.name.split(".")[1].startswith("v") else 0))
            if logs:
                yield logs[-1]
    return [_first(("dsh", session), candidates)]


_dsh_cache = {}


def _dsh_rows(path):
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    cached = _dsh_cache.get(str(path))
    if cached and cached[0] == signature:
        return cached[1]
    raw = path.read_bytes()
    if path.suffix == ".zstd":
        from compression import zstd  # Python 3.14+: Desk bundles it; readable() keeps a copy without it.
        raw = zstd.decompress(raw)
    rows = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    _dsh_cache.clear()
    _dsh_cache[str(path)] = (signature, rows)
    return rows


DSH_TURN = {"completed": "completed", "cancelled": "cancelled", "aborted": "cancelled", "interrupted": "cancelled", "failed": "failed", "error": "failed"}


def _dsh(call, files, window):
    events, usage = [], {}
    for row in _dsh_rows(files[0]):
        at = row["time"] / 1000 if isinstance(row.get("time"), (int, float)) else None
        if not _within(at, window):
            continue
        kind, data = row.get("type"), row.get("data") or {}
        if kind == "assistant/message":
            message = data.get("message") or {}
            for number, part in enumerate(message.get("content") or []):
                if part.get("type") == "reasoning" and str(part.get("text") or "").strip():
                    events.append({"kind": "thought", "id": f"{row.get('seq')}:{number}", "text": part["text"]})
                elif part.get("type") == "text" and str(part.get("text") or "").strip():
                    events.append({"kind": "message", "id": f"{row.get('seq')}:{number}", "text": part["text"]})
            spent = data.get("usage") or {}
            if spent:  # Its inputTokens leave out cache reads and writes.
                cached = spent.get("cacheReadTokens") or 0
                _add(usage, (spent.get("inputTokens") or 0) + cached + (spent.get("cacheWriteTokens") or 0), cached, spent.get("outputTokens") or 0)
        elif kind == "tool/call":
            events.append(_call(data.get("callId"), data.get("name"), data.get("arguments")))
        elif kind == "tool/result":
            message = data.get("message") or {}
            events.append(_result(message.get("toolCallId"), message.get("content"), bool(message.get("isError"))))
        elif kind == "turn/end":
            reason = data.get("reason") or {}
            events.append({"kind": "turn", "status": DSH_TURN.get(reason.get("kind") if isinstance(reason, dict) else reason, "completed")})
    return events, usage


# ---- Nanobot ------------------------------------------------------------------------------

def _nanobot_files(call):
    session = session_of(call)

    def candidates():
        for path in (Path.home() / ".nanobot" / "sessions").glob("*/sessions.sqlite3"):
            try:
                with closing(_database(path)) as db:
                    if db.execute("SELECT 1 FROM sessions WHERE key=?", (session,)).fetchone():
                        yield path
            except Exception:  # noqa: BLE001  Another layout or a locked file: look elsewhere.
                continue
    found = _first(("nanobot", session), candidates)
    return [found, Path(str(found) + "-wal")]


def _nanobot(call, files, window):
    """Nanobot's messages carry no reliable times: the n-th user message is the n-th call's prompt."""
    with closing(_database(files[0])) as db:
        rows = [json.loads(r[0]) for r in db.execute("SELECT record FROM messages WHERE session_key=? ORDER BY position", (session_of(call),))]
    turns = []
    for record in rows:
        if record.get("role") == "user":
            turns.append([])
        elif turns:
            turns[-1].append(record)
    mine = turns[window["index"]:] if window["last"] else turns[window["index"]:window["index"] + 1]
    events = []
    for turn in mine:
        for number, record in enumerate(turn):
            if record.get("role") == "assistant":
                if str(record.get("reasoning_content") or "").strip():
                    events.append({"kind": "thought", "text": record["reasoning_content"]})
                if str(record.get("content") or "").strip():
                    events.append({"kind": "message", "text": record["content"]})
                for tool in record.get("tool_calls") or []:
                    function = tool.get("function") or {}
                    events.append(_call(tool.get("id"), function.get("name"), function.get("arguments")))
            elif record.get("role") == "tool":
                events.append(_result(record.get("tool_call_id"), record.get("content")))
        if window["has_result"]:
            events.append({"kind": "turn", "status": "completed"})
    return events, {}


# ---- Muse ---------------------------------------------------------------------------------

def _muse_roots(call):
    roots = [Path(os.environ["XDG_DATA_HOME"]) / "muse"] if os.environ.get("XDG_DATA_HOME") else []
    roots += [Path.home() / ".local" / "share" / "muse"]
    roots += [Path(os.environ[name]) / "muse" for name in ("LOCALAPPDATA", "APPDATA") if os.environ.get(name)]
    # A launcher may give Muse a private data folder under the AutoSwarm home it serves.
    homes = [Path(call["directory"]).parent.parent] if call.get("directory") else []
    homes += [Path(os.environ["AUTOSWARM_HOME"])] if os.environ.get("AUTOSWARM_HOME") else []
    for home in homes:
        roots += list((home / "native-profiles").glob("*/data/muse"))
    return roots


def _muse_files(call):
    session = session_of(call)
    return [_first(("muse", session), lambda: _glob([r / "sessions" for r in _muse_roots(call)], f"*/*/*/{session}/session.jsonl"))]


MUSE_TURN = {"completed": "completed", "cancelled": "cancelled", "interrupted": "cancelled", "failed": "failed"}


def _muse(call, files, window):
    events, usage = [], {}
    for line in _jsonl(files[0]):
        records = [json.loads(child["record_json"]) for child in line.get("children", []) if isinstance(child, dict) and "record_json" in child] \
            if "retained_frame" in line else [line]
        for record in records:
            at = record["recorded_at"] / 1e6 if isinstance(record.get("recorded_at"), (int, float)) else None
            payload = record.get("payload") or {}
            if not _within(at, window) or record.get("payload_type") != "runtime.session" or payload.get("kind") != "run":
                continue
            event = payload.get("event") or {}
            kind = event.get("kind")
            if kind == "reasoning_summary_committed" and str(event.get("text") or "").strip():
                events.append({"kind": "thought", "id": event.get("message_id"), "text": event["text"]})
            elif kind == "assistant_message_committed" and str(event.get("text") or "").strip():
                events.append({"kind": "message", "id": event.get("message_id"), "text": event["text"]})
            elif kind == "assistant_tool_calls_committed":
                for tool in event.get("tool_calls") or []:
                    events.append(_call(tool.get("call_id") or tool.get("id"), tool.get("name"), tool.get("args") or tool.get("arguments")))
            elif kind == "tool_result_batch_committed":
                for result in event.get("results") or []:
                    text = result.get("text") or ""
                    events.append(_result(result.get("tool_call_id"), text, bool(result.get("is_error")) or str(text).startswith("tool failed")))
            elif kind == "model_completed":
                spent = event.get("usage") or {}
                _add(usage, spent.get("input_tokens") or 0, spent.get("cache_read_tokens") or 0, spent.get("output_tokens") or 0)
            elif kind == "terminal":
                events.append({"kind": "turn", "status": MUSE_TURN.get(event.get("terminal"), "failed")})
    return events, usage


READERS = {"codebuddy": (_codebuddy_files, _codebuddy), "copilot": (_copilot_files, _copilot), "kimi": (_kimi_files, _kimi),
           "grok": (_grok_files, _grok), "opencode": (_opencode_files, _opencode), "hermes": (_hermes_files, _hermes),
           "dsh": (_dsh_files, _dsh), "nanobot": (_nanobot_files, _nanobot), "muse": (_muse_files, _muse)}


def files(call):
    """The record and any companion file whose change means new events (a SQLite WAL); Missing when absent."""
    return READERS[call["task"]["provider"]][0](call)


def read(call, start, end, index, last, has_result):
    paths = files(call)
    events, usage = READERS[call["task"]["provider"]][1](call, paths, {"start": start, "end": end, "index": index, "last": last, "has_result": has_result})
    return events, paths, usage
