"""Stateless native protocol lenses. Unknown data is retained, never executed."""
import json

TOOLS = {"read": "read", "readfile": "read", "glob": "search", "grep": "search", "ls": "search", "edit": "edit", "write": "edit", "applypatch": "edit", "patch": "edit", "bash": "execute", "shell": "execute", "command": "execute", "webfetch": "fetch", "websearch": "search", "todowrite": "think"}
# Every agent AutoSwarm reaches over ACP (Kimi Code too), from the one list that launches them.
from autoswarm.providers import ACP_PROVIDERS as ACP

# Protocol traffic with nothing to show in a conversation: an agent's command list, session title,
# settings and mode changes. Permission requests are shown as approval cards, not in the stream.
ACP_QUIET = {"available_commands_update", "session_info_update", "config_option_update", "current_mode_update"}


def tool_kind(name):
    return TOOLS.get(str(name or "").lower().replace("_", ""), "other")


def plain(title):
    """Cursor titles a command as inline code (`dir`); the stream shows the command itself."""
    if isinstance(title, str) and len(title) > 2 and title[0] == title[-1] == "`" and title[1] != "`":
        return title[1:-1]
    return title


def acp_title(update):
    """A tool's own title, unless it is only the tool's name: then what it runs or touches."""
    title = plain(update.get("title"))
    given = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
    named = isinstance(title, str) and title and " " not in title.strip()
    for key in ("command", "cmd", "file_path", "path", "url", "query", "pattern"):
        value = given.get(key)
        if isinstance(value, list):
            value = " ".join(map(str, value))
        if isinstance(value, str) and value.strip() and (named or not title):
            return value.strip()
    return title


def event(kind, **fields):
    return {"kind": kind, **{k: v for k, v in fields.items() if v is not None}}


def normalize(provider, value, session=None, state=None):
    """Malformed and newly introduced events are inspectable notices. `state`
    (one dict per page of lines) lets Claude's partial-message deltas and the
    complete blocks that follow them share an id."""
    raw = value
    try:
        if not isinstance(value, dict):
            raise ValueError("Native event is not an object")
        if value.get("autoswarm") == "capture_truncated":  # Written by AutoSwarm's capture, not the agent.
            size = value.get("limit_bytes")
            limit = f" {round(size / 1048576)} MB" if isinstance(size, (int, float)) and size > 0 else ""
            return [event("notice", title=f"过程记录达到{limit}上限，之后的过程未保存", truncated=True, raw=raw)]
        if value.get("autoswarm"):  # Other AutoSwarm marks (e.g. "prompt") are read by the pager.
            return []
        if provider == "claude-code" and value.get("type") == "stream_event":
            return [{**item, "raw": raw} for item in _claude_partial(value, state)]
        if provider == "opencode" and isinstance(value.get("payload"), dict):
            value = value["payload"]
        if provider == "opencode":
            props = value.get("properties") or {}
            sid = props.get("sessionID") or (props.get("part") or {}).get("sessionID")
            if session and sid and sid != session:
                return []
        events = _map(provider, value, state)
        if events is None:  # Known protocol traffic with nothing to show.
            return []
        if not events:
            events = [event("notice", title=value.get("method") or value.get("type") or "原生事件")]
        return [{**item, "raw": raw} for item in events]
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return [event("notice", title="无法识别的原生事件", raw=raw)]


def _claude_partial(value, state):
    """--include-partial-messages: text and thinking as they are written. The
    message id and block index match the complete block that follows."""
    e = value.get("event") or {}
    mid, index = value.get("api_message_id") or (e.get("message") or {}).get("id"), e.get("index")
    if e.get("type") == "content_block_start":
        if state is not None and mid:
            state[mid] = index
        block = e.get("content_block") or {}
        if block.get("type") == "tool_use":
            return [event("tool", id=block.get("id"), title=block.get("name"), tool=tool_kind(block.get("name")), status="in_progress")]
    if e.get("type") == "content_block_delta" and mid is not None:
        delta = e.get("delta") or {}
        if delta.get("type") == "text_delta":
            return [event("message", id=f"{mid}:{index}", text=delta.get("text", ""))]
        if delta.get("type") == "thinking_delta":
            return [event("thought", id=f"{mid}:{index}", text=delta.get("thinking", ""))]
    return []


def _map(provider, v, state=None):
    m, p = v.get("method"), v.get("params") or {}
    t = v.get("type")
    if provider == "opencode" and (m == "session/update" or isinstance(v.get("result"), dict) and "stopReason" in v["result"]):
        return _map("cursor", v)  # OpenCode v2's native ACP transport.
    if provider in ACP:
        if m == "session/update":
            u = p["update"]
            kind = u.get("sessionUpdate")
            if kind in {"agent_message_chunk", "agent_thought_chunk", "user_message_chunk"}:
                return [event({"agent_message_chunk": "message", "agent_thought_chunk": "thought", "user_message_chunk": "user"}[kind], text=u.get("content", {}).get("text", ""))]
            if kind in {"tool_call", "tool_call_update"}:
                return [event("tool", id=u.get("toolCallId"), title=acp_title(u), tool=u.get("kind"), status=u.get("status"), output=u.get("content"), input=u.get("rawInput"))]
            if kind in {"plan", "usage_update"}:
                return [event("plan" if kind == "plan" else "usage", entries=u.get("entries"))]
            if kind in ACP_QUIET:
                return None
        if isinstance(v.get("result"), dict) and "stopReason" in v["result"]:
            return [event("turn", status=v["result"]["stopReason"])]
        if isinstance(m, str) and m.startswith("_"):
            return None  # An agent's own extension notifications (Grok's _x.ai/…, CodeBuddy's _codebuddy.ai/…).
        if m is None and "id" in v:
            # Replies to AutoSwarm's requests: setup is protocol; a failed prompt is the agent's error.
            error = v.get("error")
            return [event("error", text=error.get("message") if isinstance(error, dict) and error.get("message") else json.dumps(error, ensure_ascii=False))] if error else None
        if m and "id" in v:
            return None  # Requests to AutoSwarm (permissions, questions) appear as approval cards.
    if provider in {"codex", "muse"}:
        if m in {"item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/reasoning/textDelta"}:
            return [event("message" if "agentMessage" in m else "thought", id=p.get("itemId"), text=p.get("delta"))]
        if m == "item/commandExecution/outputDelta":
            return [event("tool", id=p.get("itemId"), output=p.get("delta"), outputDelta=True)]
        if m in {"item/started", "item/completed"}:
            item = p.get("item") or {}
            kind = item.get("type") or item.get("kind")
            done = m == "item/completed"
            if kind == "agentMessage":
                return [event("message", id=item.get("id") or item.get("itemId"), text=item.get("text", ""), replace=True, final=item.get("phase") == "final_answer")]
            if kind == "reasoning":
                parts = item.get("summary") or item.get("content") or ""
                text = "\n".join(str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in parts) if isinstance(parts, list) else str(parts)
                return [event("thought", id=item.get("id") or item.get("itemId"), text=text, replace=True)]
            if kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch", "toolCall"}:
                return [event("tool", id=item.get("id") or item.get("itemId"), title=item.get("command") or item.get("tool") or item.get("query") or kind,
                              tool={"commandExecution": "execute", "fileChange": "edit", "webSearch": "search"}.get(kind, tool_kind(item.get("tool"))),
                              status={"inProgress": "in_progress", "declined": "failed"}.get(item.get("status"), item.get("status")) or ("completed" if done else "in_progress"),
                              input=item.get("args"), output=item.get("aggregatedOutput") or item.get("result") or item.get("visibleOutput"), changes=item.get("changes"))]
        if m in {"turn/plan/updated", "thread/tokenUsage/updated"}:
            return [event("plan" if "plan" in m else "usage", entries=p.get("plan"))]
        if m == "turn/completed":
            return [event("turn", status=(p.get("turn") or {}).get("status") or p.get("terminal"))]
        if m == "error":
            return [event("error", text=json.dumps(p, ensure_ascii=False))]
    if provider == "claude-code":
        if t in {"assistant", "user"}:
            result = []
            message = v.get("message") or {}
            content = message.get("content", [])
            for index, block in enumerate(content):
                bt = block.get("type")
                mid = message.get('id')
                # Claude sends one block per event, each numbered 0. With partial
                # messages the block's real index was seen at content_block_start
                # (so deltas and this block share an id); otherwise the event's
                # uuid keeps a message's thinking and text apart.
                if mid and state is not None and mid in state and len(content) == 1:
                    bid = f"{mid}:{state[mid]}"
                else:
                    bid = f"{v.get('uuid') or mid}:{index}" if (v.get('uuid') or mid) else None
                if bt in {"text", "thinking"}:
                    result.append(event("thought" if bt == "thinking" else "message" if t == "assistant" else "user", id=bid, text=block.get("text") or block.get("thinking"), replace=True))
                elif bt == "tool_use":
                    inp = block.get("input") or {}
                    result.append(event("tool", id=block.get("id"), title=inp.get("command") or inp.get("file_path") or block.get("name"), tool=tool_kind(block.get("name")), input=inp, status="in_progress"))
                elif bt == "tool_result":
                    result.append(event("tool", id=block.get("tool_use_id"), status="failed" if block.get("is_error") else "completed", output=block.get("content")))
                else:
                    result.append(event("notice", title=str(bt)))
            return result
        if t == "result":
            return [*([event("error", text=v.get("result") or str(v.get("errors")))] if v.get("is_error") else []), event("usage", usage=v.get("usage")), event("turn", status="failed" if v.get("is_error") else "completed")]
        if t in {"system", "rate_limit_event", "control_response"}:
            return None  # Hooks, init, status, thinking-token counts: nothing to show.
    if provider == "opencode":
        props = v.get("properties") or {}
        if t == "message.part.updated":
            part = props.get("part") or {}
            kind = part.get("type")
            if kind in {"text", "reasoning"}:
                return [event("message" if kind == "text" else "thought", id=part.get("id"), text=part.get("text"), replace=True)]
            if kind == "tool":
                state = part.get("state") or {}
                return [event("tool", id=part.get("id") or part.get("callID"), title=state.get("title") or part.get("tool"), tool=tool_kind(part.get("tool")), input=state.get("input"), output=state.get("output") or state.get("error"), status={"running": "in_progress", "error": "failed"}.get(state.get("status"), state.get("status")))]
        if t == "message.part.delta" and props.get("field") in {"text", "reasoning"}:
            return [event("thought" if props["field"] == "reasoning" else "message", id=props.get("partID"), text=props.get("delta"))]
        if t == "session.idle":
            return [event("turn", status="completed")]
        if t == "session.error":
            return [event("error", text=json.dumps(props, ensure_ascii=False))]
    return []
