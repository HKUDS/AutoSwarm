"""Desk reads Claude Code, Codex and Cursor calls from the agents' own session records.

The stores below follow the shapes recorded from Claude Code 2.1.316, Codex
0.166 (app-server, thread_history projection and rollout) and Cursor Agent
2026.10 (ACP store.db) on this project's acceptance runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm.desk import native_calls, native_history
from autoswarm.desk.watch import Scanner
from autoswarm.records import write_json

T0, T1 = "2026-11-02T08:00:00+00:00", "2026-11-02T08:10:00+00:00"
EPOCH0 = 1793606400  # 2026-11-02T08:00:00Z


def call(cid, provider, session, created_at, result=True, workspace="D:\\验收 工作区"):
    return {"id": cid, "task": {"call_id": cid, "agent_id": "call_a", "provider": provider, "created_at": created_at, "workspace": workspace},
            "handle": {"native_session_id": session}, "result": {"outcome": "completed", "content": "ok"} if result else None}


class NativeCallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        env = {"CLAUDE_CONFIG_DIR": str(self.root / "claude"), "CODEX_HOME": str(self.root / "codex"),
               "CURSOR_CONFIG_DIR": str(self.root / "cursor"), "LOCALAPPDATA": str(self.root / "local")}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        home = patch.object(Path, "home", lambda: self.root / "nobody")
        home.start()
        self.addCleanup(home.stop)
        native_calls._located.clear(); native_calls._searched.clear(); native_calls._files.clear(); native_history._claude_cache.clear()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    # ---- Claude Code -------------------------------------------------------
    def claude(self, session="s-claude", workspace="D:\\验收 工作区", project=None):
        folder = self.root / "claude/projects" / (project or "D---------")
        folder.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "queue-operation", "timestamp": "2026-11-02T08:00:01Z"},
            {"type": "user", "uuid": "u1", "timestamp": "2026-11-02T08:00:01Z", "message": {"role": "user", "content": "第一轮的提示"}},
            {"type": "attachment", "timestamp": "2026-11-02T08:00:01Z"},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-11-02T08:00:03Z", "message": {"id": "msg_1", "usage": {"input_tokens": 3, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 40}, "content": [{"type": "thinking", "thinking": "先看看目录"}]}},
            {"type": "assistant", "uuid": "a2", "timestamp": "2026-11-02T08:00:03Z", "message": {"id": "msg_1", "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "user", "uuid": "r1", "timestamp": "2026-11-02T08:00:04Z", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "a.txt"}]}},
            {"type": "assistant", "uuid": "s1", "isSidechain": True, "timestamp": "2026-11-02T08:00:04Z", "message": {"id": "msg_x", "content": [{"type": "text", "text": "子 agent 的话"}]}},
            {"type": "assistant", "uuid": "a3", "timestamp": "2026-11-02T08:00:05Z", "message": {"id": "msg_1", "usage": {"input_tokens": 3, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 40}, "content": [{"type": "text", "text": "目录里有 a.txt。"}]}},
            {"type": "user", "uuid": "u2", "timestamp": "2026-11-02T08:10:02Z", "message": {"role": "user", "content": "Respond to the incoming human messages"}},
            {"type": "assistant", "uuid": "a4", "timestamp": "2026-11-02T08:10:04Z", "message": {"id": "msg_2", "content": [{"type": "text", "text": "第二轮的回答"}]}},
        ]
        path = folder / f"{session}.jsonl"
        path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
        return path

    def test_claude_turns_are_split_by_call_and_keep_tools_thinking_and_replies(self):
        self.claude()
        calls = {c["id"]: c for c in (call("call_1", "claude-code", "s-claude", T0), call("call_2", "claude-code", "s-claude", T1))}
        first = native_calls.page("call_1", calls)
        self.assertTrue(first["available"])
        self.assertEqual([(e["kind"], e.get("title") or e.get("text")) for e in first["events"]],
                         [("thought", "先看看目录"), ("tool", "ls"), ("tool", None), ("message", "目录里有 a.txt。"), ("turn", None)])
        self.assertEqual(first["events"][2]["output"], "a.txt", "the tool result joins its tool by id")
        self.assertNotEqual(first["events"][0]["id"], first["events"][3]["id"], "blocks of one message on separate lines keep separate ids")
        second = native_calls.page("call_2", calls)
        self.assertEqual([(e["kind"], e.get("text")) for e in second["events"]], [("message", "第二轮的回答"), ("turn", None)])
        self.assertEqual([e["seq"] for e in second["events"]], ["0:0", "1:0"])
        self.assertEqual(first["usage"], {"input": 1003, "cached": 900, "output": 40}, "one API message split over lines is counted once")
        self.assertNotIn("usage", second)

    def test_claude_is_found_wherever_its_build_kept_it(self):
        # Long or unusual paths: found by session id in any project folder.
        self.claude(project="some-other-name")
        calls = {"call_1": call("call_1", "claude-code", "s-claude", T0)}
        self.assertTrue(native_calls.page("call_1", calls)["available"])
        # Claude desktop's local agent mode keeps a private config dir per session.
        native_calls._located.clear()
        desktop = self.root / "local/Packages/Claude_x/LocalCache/Local/Claude-3p/local-agent-mode-sessions/org/0/abc/.claude/projects/session"
        desktop.mkdir(parents=True)
        (self.claude(session="s-desktop")).replace(desktop / "s-desktop.jsonl")
        calls = {"call_9": call("call_9", "claude-code", "s-desktop", T0)}
        self.assertTrue(native_calls.page("call_9", calls)["available"])

    def test_a_claude_code_host_conversation_reads_as_turns(self):
        """An external main conversation: the user's own Claude Code session that dispatched AutoSwarm workers."""
        path = self.claude()
        binding = {"host": "claude-code", "session_id": "s-claude"}
        value = native_history.conversation(binding)
        turns = value["turns"]
        self.assertEqual([t["user"]["text"] for t in turns], ["第一轮的提示", "Respond to the incoming human messages"])
        self.assertEqual((turns[0]["status"], turns[0]["final"]["text"]), ("completed", "目录里有 a.txt。"))
        self.assertEqual([(i["kind"], i.get("title") or i.get("text"), i.get("output")) for i in turns[0]["items"]],
                         [("thought", "先看看目录", None), ("tool", "ls", "a.txt")], "the sidechain line is not part of the host's turn")
        self.assertTrue(value["busy"], "the last turn has not ended yet")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "system", "subtype": "turn_duration", "durationMs": 2000, "timestamp": "2026-11-02T08:10:05Z"}) + "\n")
        value = native_history.conversation(binding)
        self.assertEqual((value["busy"], value["turns"][-1]["status"], value["turns"][-1]["duration_ms"], value["turns"][-1]["final"]["text"]),
                         (False, "completed", 2000, "第二轮的回答"))
        summary = native_history.conversation(binding, detail=False)
        self.assertEqual((summary["title"], summary["provider"], "turns" in summary), ("第一轮的提示", "claude-code", False))
        self.assertEqual(native_history.conversation({"host": "claude-code", "session_id": "nowhere"}), {})

    def test_a_missing_record_says_so_and_appends_are_read_live(self):
        calls = {"call_1": call("call_1", "claude-code", "s-claude", T0, result=False)}
        early = native_calls.page("call_1", calls)
        self.assertEqual((early["available"], early["source"]), (False, "native"))
        self.assertNotIn("missing", early, "a running agent may not have written its record yet")
        finished = {"call_1": call("call_1", "claude-code", "s-claude", T0)}
        self.assertIn("Claude Code", native_calls.page("call_1", finished)["missing"])
        path = self.claude()
        mark = native_calls.signature("call_1", calls)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "assistant", "uuid": "a9", "timestamp": "2026-11-02T08:09:00Z", "message": {"id": "msg_9", "content": [{"type": "text", "text": "还在做"}]}}, ensure_ascii=False) + "\n")
            stream.write('{"type": "assistant", "unfinished')  # A partial line is not read yet.
        self.assertNotEqual(native_calls.signature("call_1", calls), mark)
        events = native_calls.page("call_1", calls)["events"]
        self.assertEqual(events[-1]["text"], "还在做", "a running call has no turn end and sees appended lines")

    # ---- Codex ------------------------------------------------------------------
    def codex_history(self, thread="t-codex"):
        root = self.root / "codex"
        root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(root / "thread_history_1.sqlite")
        db.execute("CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, rollout_ordinal INTEGER, status TEXT, started_at INTEGER, completed_at INTEGER)")
        db.execute("CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, rollout_ordinal INTEGER, created_at_ms INTEGER, item_json TEXT, item_type TEXT)")
        db.executemany("INSERT INTO thread_turns VALUES (?,?,?,?,?,?)", [(thread, "turn-1", 1, "completed", EPOCH0 + 2, EPOCH0 + 60), (thread, "turn-2", 50, "failed", EPOCH0 + 602, EPOCH0 + 650)])
        items = [("turn-1", {"type": "userMessage", "id": "u1", "content": [{"type": "text", "text": "提示"}]}),
                 ("turn-1", {"type": "agentMessage", "id": "m1", "text": "我先看一下。", "phase": "commentary"}),
                 ("turn-1", {"type": "commandExecution", "id": "e1", "command": "powershell.exe -Command Get-ChildItem", "commandActions": [{"type": "unknown", "command": "Get-ChildItem"}], "aggregatedOutput": "a.txt", "exitCode": 0, "status": "completed"}),
                 ("turn-1", {"type": "agentMessage", "id": "m2", "text": "完成。", "phase": "final_answer"}),
                 ("turn-2", {"type": "agentMessage", "id": "m3", "text": "第二轮", "phase": "final_answer"})]
        db.executemany("INSERT INTO thread_items VALUES (?,?,?,?,?,?,?)", [(thread, turn, item["id"], n, (EPOCH0 + n) * 1000, json.dumps(item, ensure_ascii=False), item["type"]) for n, (turn, item) in enumerate(items)])
        db.commit(); db.close()

    def codex_rollout(self, thread="t-codex", old=False):
        root = self.root / "codex"
        folder = root / "sessions/2026/11/02"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = lambda s: f"2026-11-02T08:00:{s:02d}.000Z"
        if old:  # Builds before item_completed: Responses API items only.
            lines = [{"timestamp": stamp(1), "type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
                     {"timestamp": stamp(2), "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "提示"}]}},
                     {"timestamp": stamp(3), "type": "response_item", "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "**先列目录**"}]}},
                     {"timestamp": stamp(4), "type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "c1", "arguments": json.dumps({"command": ["powershell.exe", "-Command", "dir"]})}},
                     {"timestamp": stamp(5), "type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": json.dumps({"output": "a.txt"})}},
                     {"timestamp": stamp(6), "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "完成。"}]}},
                     {"timestamp": stamp(7), "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1"}}]
        else:
            lines = [{"timestamp": stamp(1), "type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
                     {"timestamp": stamp(2), "type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "UserMessage", "id": "u1", "content": [{"type": "text", "text": "提示"}]}}},
                     {"timestamp": stamp(3), "type": "event_msg", "payload": {"type": "item_completed", "completed_at_ms": 1, "item": {"type": "CommandExecution", "id": "e1", "command": ["powershell.exe", "-Command", "Get-Content a.txt"], "parsed_cmd": [{"type": "read", "cmd": "Get-Content a.txt", "name": "a.txt", "path": "a.txt"}], "aggregated_output": "A", "exit_code": 0, "status": "completed", "duration": {"secs": 1, "nanos": 500000000}}}},
                     {"timestamp": stamp(4), "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "重复的一份"}]}},
                     {"timestamp": stamp(4), "type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 30}}}},
                     {"timestamp": stamp(5), "type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "AgentMessage", "id": "m1", "content": [{"type": "Text", "text": "进行中"}], "phase": "commentary"}}}]
        path = folder / f"rollout-2026-11-02T16-00-00-{thread}.jsonl"
        path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
        return path

    def test_codex_finished_calls_read_the_thread_history_projection(self):
        self.codex_history()
        calls = {c["id"]: c for c in (call("call_1", "codex", "t-codex", T0), call("call_2", "codex", "t-codex", T1))}
        first = native_calls.page("call_1", calls)["events"]
        self.assertEqual([(e["kind"], e.get("title") or e.get("text"), e.get("final")) for e in first],
                         [("message", "我先看一下。", False), ("tool", "Get-ChildItem", None), ("message", "完成。", True), ("turn", None, None)])
        self.assertEqual(first[1]["output"], "a.txt")
        second = native_calls.page("call_2", calls)["events"]
        self.assertEqual([(e["kind"], e.get("status")) for e in second], [("message", None), ("turn", "failed")])

    def test_codex_running_calls_read_the_live_rollout_new_and_old_formats(self):
        self.codex_history()
        self.codex_rollout()
        running = {"call_1": call("call_1", "codex", "t-codex", T0, result=False)}
        events = native_calls.page("call_1", running)["events"]
        self.assertEqual([(e["kind"], e.get("title") or e.get("text")) for e in events], [("tool", "读取 a.txt"), ("message", "进行中")])
        self.assertEqual((events[0]["output"], events[0]["duration"]), ("A", 1500))
        self.assertEqual(native_calls.page("call_1", running)["usage"], {"input": 1200, "cached": 1000, "output": 30})
        native_calls._located.clear(); native_calls._files.clear()
        (self.root / "codex/thread_history_1.sqlite").unlink()
        self.codex_rollout(old=True)
        finished = {"call_1": call("call_1", "codex", "t-codex", T0)}
        old = native_calls.page("call_1", finished)["events"]
        self.assertEqual([(e["kind"], e.get("title") or e.get("text") or e.get("output")) for e in old],
                         [("thought", "**先列目录**"), ("tool", "powershell.exe -Command dir"), ("tool", "a.txt"), ("message", "完成。"), ("turn", None)])

    # ---- Cursor --------------------------------------------------------------------
    def cursor(self, session="c-cursor", place="acp-sessions"):
        folder = self.root / "cursor" / place / session
        folder.mkdir(parents=True)
        db = sqlite3.connect(folder / "store.db")
        db.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        messages = [
            {"role": "system", "content": "You are an AI coding assistant"},
            {"role": "user", "content": "<user_info>OS Version: win32</user_info>"},
            {"role": "user", "content": [{"type": "text", "text": "<timestamp>Monday</timestamp>\n<user_query>\n建文件\n</user_query>"}]},
            {"role": "assistant", "id": "1", "content": [{"type": "reasoning", "text": ""}, {"type": "text", "text": "用终端创建。"}, {"type": "tool-call", "toolCallId": "t1", "toolName": "Shell", "args": {"command": "printf R > r.txt"}}]},
            {"role": "tool", "id": "2", "content": [{"type": "tool-result", "toolCallId": "t1", "toolName": "Shell", "result": "Exit code: 0"}]},
            {"role": "assistant", "id": "3", "content": [{"type": "text", "text": "已创建 r.txt。"}]},
            {"role": "user", "content": [{"type": "text", "text": "<user_query>\nRespond to the incoming human messages\n</user_query>"}]},
            {"role": "assistant", "id": "4", "content": [{"type": "text", "text": "一句话总结。"}]},
        ]
        ids = []
        for message in messages:
            data = json.dumps(message, ensure_ascii=False).encode()
            blob_id = hashlib.sha256(data).hexdigest()
            ids.append(blob_id)
            db.execute("INSERT INTO blobs VALUES (?,?)", (blob_id, data))
        root = b"".join(b"\x0a\x20" + bytes.fromhex(i) for i in ids) + b"\x32\x03cli"
        root_id = hashlib.sha256(root).hexdigest()
        db.execute("INSERT INTO blobs VALUES (?,?)", (root_id, root))
        db.execute("INSERT INTO meta VALUES ('0', ?)", (json.dumps({"agentId": session, "latestRootBlobId": root_id}).encode().hex(),))
        db.commit(); db.close()

    def test_cursor_turns_follow_prompt_order(self):
        self.cursor()
        calls = {c["id"]: c for c in (call("call_1", "cursor", "c-cursor", T0), call("call_2", "cursor", "c-cursor", T1))}
        first = native_calls.page("call_1", calls)["events"]
        self.assertEqual([(e["kind"], e.get("title") or e.get("text") or e.get("output"), e.get("tool")) for e in first],
                         [("message", "用终端创建。", None), ("tool", "printf R > r.txt", "execute"), ("tool", "Exit code: 0", None), ("message", "已创建 r.txt。", None), ("turn", None, None)])
        self.assertEqual([(e["kind"], e.get("text")) for e in native_calls.page("call_2", calls)["events"]], [("message", "一句话总结。"), ("turn", None)])
        native_calls._located.clear()
        (self.root / "cursor").rename(self.root / "elsewhere")
        self.cursor(place="chats/0123abcd")  # The agent's terminal chats use the same store.
        self.assertTrue(native_calls.page("call_1", calls)["available"])

    # ---- the watcher ----------------------------------------------------
    def test_the_watcher_offers_native_records_and_the_reader_falls_back_to_captured_events(self):
        home = self.root / "records"
        self.claude()
        for cid, created in (("call_1", T0), ("call_2", T1)):
            directory = home / "calls" / cid
            write_json(directory / "task.json", {"call_id": cid, "agent_id": "call_1", "provider": "claude-code", "created_at": created, "workspace": "D:\\验收 工作区", "prompt": "p"})
            write_json(directory / "handle.json", {"native_session_id": "s-claude"})
            write_json(directory / "result.json", {"outcome": "completed", "content": "ok"})
        (home / "calls/call_2/events.jsonl").write_text('{"type":"assistant","message":{"content":[{"type":"text","text":"captured"}]}}\n', encoding="utf-8")
        snapshot = Scanner(home).scan()
        self.assertEqual(snapshot["calls"]["call_1"]["stream"], {"available": True, "lazy": True, "source": "native"})
        calls = native_calls.load_calls(home)
        self.assertEqual(native_calls.page("call_2", calls)["events"][0]["text"], "第二轮的回答", "the agent's own record wins over the copy")
        native_calls._located.clear()
        (self.root / "claude").rename(self.root / "gone")
        self.assertFalse(native_calls.page("call_2", calls)["available"])


if __name__ == "__main__":
    unittest.main()
