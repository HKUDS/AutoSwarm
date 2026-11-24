"""Desk reads every agent's process from that agent's own records: each call gets exactly its part of the session.

The records here have the shapes the agents write (trimmed): two calls of one native session each, the
first call's last words written just before the second call starts.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from autoswarm import capture
from autoswarm.desk import agent_records, native_calls

T0 = 1_795_000_000.0  # Seconds; the first call starts here, the second at T0 + 10.4 (after the first ended at T0 + 10).


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def calls(provider, session, home, transport=None):
    def one(name, created, finished):
        task = {"provider": provider, "created_at": iso(created), "workspace": str(home), **({"native_transport": transport} if transport else {})}
        return {"id": name, "task": task, "handle": {"native_session_id": session}, "result": {"outcome": "completed", "finished_at": iso(finished)},
                "directory": str(home / "records" / "calls" / name)}
    return {"call_a": one("call_a", T0, T0 + 10), "call_b": one("call_b", T0 + 10.4, T0 + 20)}


class AgentRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-records-中文 ")
        self.root = Path(self.temp.name)
        native_calls._located.clear()
        self.env = patch.dict(os.environ, {"CODEBUDDY_CONFIG_DIR": str(self.root / "codebuddy"), "COPILOT_HOME": str(self.root / "copilot"),
                                           "KIMI_CODE_HOME": str(self.root / "kimi"), "GROK_HOME": str(self.root / "grok"),
                                           "XDG_DATA_HOME": str(self.root / "data"), "HERMES_HOME": str(self.root / "hermes"),
                                           "DSH_HOME": str(self.root / "dsh")})
        self.env.start()
        self.home = patch("autoswarm.desk.agent_records.Path.home", return_value=self.root / "user")
        self.home.start()

    def tearDown(self):
        self.home.stop()
        self.env.stop()
        native_calls._located.clear()
        self.temp.cleanup()

    def check(self, provider, session, transport=None, usage=True):
        both = calls(provider, session, self.root, transport)
        first, second = native_calls.page("call_a", both), native_calls.page("call_b", both)
        for page, marker, other in ((first, "first", "second"), (second, "second", "first")):
            self.assertTrue(page["available"], (provider, page))
            text = json.dumps(page["events"], ensure_ascii=False)
            self.assertIn(f"echo {marker}-call-marker", text, provider)
            self.assertNotIn(f"{other}-call-marker", text, f"{provider}: each call shows only its own part")
            kinds = [event["kind"] for event in page["events"]]
            self.assertIn("message", kinds, provider)
            self.assertTrue(any(e["kind"] == "tool" and e.get("output") and marker in json.dumps(e["output"]) for e in page["events"]), provider)
            self.assertEqual(kinds[-1], "turn", provider)
            if usage:
                self.assertGreater(page["usage"]["input"], 0, provider)
        self.assertIsNotNone(native_calls.signature("call_a", both)[0], provider)
        return first, second

    def test_codebuddy(self):
        session = "01a1f951-a54e"
        rows = []
        for marker, at in (("first", T0), ("second", T0 + 10.4)):
            ms = int(at * 1000)
            rows += [
                {"type": "message", "role": "user", "timestamp": ms + 100, "content": [{"type": "input_text", "text": "prompt"}]},
                {"type": "reasoning", "timestamp": ms + 500, "id": f"r-{marker}", "rawContent": [{"type": "reasoning_text", "text": "think"}]},
                {"type": "function_call", "timestamp": ms + 600, "callId": f"Bash_{marker}", "name": "Bash",
                 "arguments": json.dumps({"command": f"echo {marker}-call-marker"}), "providerData": {"messageId": f"m-{marker}"},
                 "message": {"usage": {"input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 80}}},
                {"type": "function_call_result", "timestamp": ms + 700, "callId": f"Bash_{marker}", "status": "completed",
                 "output": {"type": "text", "text": f"Stdout: {marker}-call-marker"}},
                {"type": "message", "role": "assistant", "timestamp": ms + 9800, "id": f"a-{marker}", "status": "completed",
                 "content": [{"type": "output_text", "text": f"{marker}-call-marker"}]}]
        jsonl(self.root / "codebuddy/projects/c-work/" / f"{session}.jsonl", rows)
        self.check("codebuddy", session)

    def test_copilot(self):
        session = "5b2752cd"
        rows = []
        for marker, at in (("first", T0), ("second", T0 + 10.4)):
            rows += [
                {"type": "user.message", "timestamp": iso(at + 0.1), "data": {"content": "prompt"}},
                {"type": "session.usage_record", "timestamp": iso(at + 1), "data": {"usage": {"inputTokens": 100, "outputTokens": 5, "cacheReadTokens": 80}}},
                {"type": "assistant.message", "timestamp": iso(at + 1.1), "data": {"messageId": f"m-{marker}", "content": "", "reasoningText": "think"}},
                {"type": "tool.execution_start", "timestamp": iso(at + 1.2), "data": {"toolCallId": f"ps_{marker}", "toolName": "powershell",
                                                                                     "arguments": {"command": f"echo {marker}-call-marker"}}},
                {"type": "tool.execution_complete", "timestamp": iso(at + 2), "data": {"toolCallId": f"ps_{marker}", "success": True,
                                                                                       "result": {"content": f"{marker}-call-marker"}}},
                {"type": "assistant.message", "timestamp": iso(at + 9.8), "data": {"messageId": f"f-{marker}", "content": f"{marker}-call-marker"}}]
        jsonl(self.root / "copilot/session-state" / session / "events.jsonl", rows)
        self.check("copilot", session)

    def test_kimi(self):
        session = "session_8ac2726b"
        rows = []
        for number, (marker, at) in enumerate((("first", T0), ("second", T0 + 10.4))):
            ms = int(at * 1000)
            loop = lambda event, time: {"type": "context.append_loop_event", "agentId": "main", "event": event, "time": time}
            rows += [
                {"type": "turn.prompt", "agentId": "main", "input": [{"type": "text", "text": "prompt"}], "time": ms + 50},
                loop({"type": "content.part", "uuid": f"t-{marker}", "part": {"type": "think", "think": "think"}}, ms + 500),
                loop({"type": "tool.call", "toolCallId": f"Bash_{marker}", "name": "Bash", "args": {"command": f"echo {marker}-call-marker"},
                      "display": {"kind": "command", "command": f"echo {marker}-call-marker"}}, ms + 600),
                loop({"type": "tool.result", "toolCallId": f"Bash_{marker}", "result": {"output": f"{marker}-call-marker\n"}}, ms + 700),
                loop({"type": "step.end", "usage": {"inputOther": 20, "output": 5, "inputCacheRead": 80, "inputCacheCreation": 0}}, ms + 800),
                loop({"type": "content.part", "uuid": f"x-{marker}", "part": {"type": "text", "text": f"{marker}-call-marker"}}, ms + 9800),
                {"type": "turn.ended", "agentId": "main", "turnId": number, "reason": "completed", "time": ms + 9900}]
        jsonl(self.root / "kimi/sessions/wd_work_1" / session / "agents/main/wire.jsonl", rows)
        self.check("kimi", session)

    def test_grok_reads_the_acp_updates_it_kept(self):
        session = "01a1f951-4a0a"
        rows = []
        for marker, at in (("first", T0), ("second", T0 + 10.4)):
            update = lambda value, time: {"timestamp": int(time), "timestamp_us": int(time * 1e6), "method": "session/update",
                                          "params": {"sessionId": session, "update": value}}
            rows += [
                update({"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "prompt"}}, at + 0.1),
                update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "think"}}, at + 0.5),
                update({"sessionUpdate": "tool_call", "toolCallId": f"run_{marker}", "title": "run_terminal_command", "kind": "execute",
                        "rawInput": {"command": f"echo {marker}-call-marker"}, "status": "pending"}, at + 0.6),
                update({"sessionUpdate": "tool_call_update", "toolCallId": f"run_{marker}", "status": "completed",
                        "content": [{"type": "content", "content": {"type": "text", "text": f"{marker}-call-marker"}}]}, at + 0.7),
                update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": f"{marker}-call-marker"}}, at + 9.8),
                {"timestamp": int(at + 9.9), "timestamp_us": int((at + 9.9) * 1e6), "method": "_x.ai/session/update",
                 "params": {"sessionId": session, "update": {"sessionUpdate": "turn_completed", "stop_reason": "end_turn",
                                                             "usage": {"inputTokens": 100, "outputTokens": 5, "cachedReadTokens": 80}}}}]
        jsonl(self.root / "grok/sessions/C%3A%5Cwork" / session / "updates.jsonl", rows)
        first, _ = self.check("grok", session)
        self.assertNotIn("user", [event["kind"] for event in first["events"]], "The prompt is the call's task, not process")

    def test_opencode_2_from_its_database(self):
        session = "ses_e06aa"
        path = self.root / "data/opencode/opencode.db"
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE session_v2 (id TEXT)")
            db.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER, time_created INTEGER, time_updated INTEGER, data TEXT)")
            db.execute("INSERT INTO session_v2 VALUES (?)", (session,))
            seq = 0
            for marker, at in (("first", T0), ("second", T0 + 10.4)):
                ms = int(at * 1000)
                assistant = {"content": [{"type": "reasoning", "text": "think"},
                                         {"type": "tool", "id": f"shell_{marker}", "name": "shell",
                                          "state": {"status": "completed", "input": {"command": f"echo {marker}-call-marker"},
                                                    "content": [{"type": "text", "text": f"{marker}-call-marker\n"}]}},
                                         {"type": "text", "text": f"{marker}-call-marker"}],
                             "tokens": {"input": 20, "output": 5, "cache": {"read": 80, "write": 0}}}
                for kind, offset, data in (("user", 50, {"text": "prompt"}), ("assistant", 9800, assistant), ("idle", 9900, {"outcome": "succeeded"})):
                    seq += 1
                    db.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)", (f"msg_{seq}", session, kind, seq, ms + offset, ms + offset, json.dumps(data)))
            db.commit()
        self.check("opencode", session, transport="acp")
        self.assertIsNone(native_calls.page("call_a", calls("opencode", session, self.root)), "OpenCode 1 (HTTP) keeps a copy instead")

    def test_hermes_matches_its_session_by_the_acp_id(self):
        acp = "local-07bd825b"
        path = self.root / "hermes/state.db"
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE sessions (id TEXT)")
            db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL, reasoning_content TEXT)")
            db.execute("INSERT INTO sessions VALUES (?)", (acp + "85aee064",))
            for marker, at in (("first", T0), ("second", T0 + 10.4)):
                call = [{"id": f"call_{marker}", "type": "function", "function": {"name": "terminal", "arguments": json.dumps({"command": f"echo {marker}-call-marker"})}}]
                for role, offset, content, extra in (("user", 0.1, "prompt", {}), ("assistant", 1, "", {"tool_calls": json.dumps(call), "reasoning_content": "think"}),
                                                     ("tool", 2, json.dumps({"output": f"{marker}-call-marker", "exit_code": 0, "error": None}), {"tool_call_id": f"call_{marker}"}),
                                                     ("assistant", 9.8, f"{marker}-call-marker", {})):
                    db.execute("INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, timestamp, reasoning_content) VALUES (?,?,?,?,?,?,?)",
                               (acp + "85aee064", role, content, extra.get("tool_call_id"), extra.get("tool_calls"), at + offset, extra.get("reasoning_content")))
            db.commit()
        self.check("hermes", acp, usage=False)

    def dsh_rows(self, session):
        rows = [{"type": "session", "version": 4, "id": session}]
        for number, (marker, at) in enumerate((("first", T0), ("second", T0 + 10.4)), 1):
            ms = int(at * 1000)
            rows += [
                {"type": "turn/start", "time": ms + 50, "data": {"turn": number}},
                {"type": "assistant/message", "seq": number * 10, "time": ms + 500, "data": {"message": {"content": [{"type": "reasoning", "text": "think"}]},
                                                                                           "usage": {"inputTokens": 20, "outputTokens": 5, "cacheReadTokens": 80}}},
                {"type": "tool/call", "time": ms + 600, "data": {"callId": f"pwsh_{marker}", "name": "pwsh", "arguments": json.dumps({"command": f"echo {marker}-call-marker"})}},
                {"type": "tool/result", "time": ms + 700, "data": {"message": {"toolCallId": f"pwsh_{marker}", "content": [{"type": "text", "text": f"{marker}-call-marker"}], "isError": False}}},
                {"type": "assistant/message", "seq": number * 10 + 5, "time": ms + 9800, "data": {"message": {"content": [{"type": "text", "text": f"{marker}-call-marker"}]}}},
                {"type": "turn/end", "time": ms + 9900, "data": {"turn": number, "reason": {"kind": "completed"}}}]
        return rows

    def test_deepseek_harness_uncompressed_log(self):
        session = "4e8a1a2f"
        jsonl(self.root / "dsh/sessions/--work--" / session / "session.v4.jsonl", self.dsh_rows(session))
        with patch("autoswarm.capture.reads_native", return_value=True), patch("autoswarm.desk.native_calls.reads_native", return_value=True):
            self.check("dsh", session)

    @unittest.skipUnless(capture.reads_native("dsh"), "Python 3.14's compression.zstd reads DeepSeek Harness' compressed logs")
    def test_deepseek_harness_reads_its_zstandard_frames(self):
        from compression import zstd
        session = "4e8a1a2f"
        lines = [json.dumps(row) + "\n" for row in self.dsh_rows(session)]
        path = self.root / "dsh/sessions/--work--" / session / "session.v4.jsonl.zstd"
        path.parent.mkdir(parents=True)
        # One frame per append, as DeepSeek Harness writes them; an older generation stays beside it.
        path.write_bytes(b"".join(zstd.compress(line.encode()) for line in lines))
        (path.parent / "session.v1.jsonl.zstd").write_bytes(zstd.compress(b'{"type": "session"}\n'))
        self.check("dsh", session)

    def test_nanobot_splits_by_its_user_messages(self):
        key = "websocket:autoswarm-fd23"
        path = self.root / "user/.nanobot/sessions/7ba1/sessions.sqlite3"
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE sessions (key TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE messages (session_key TEXT, position INTEGER, record TEXT)")
            db.execute("INSERT INTO sessions VALUES (?)", (key,))
            position = 0
            for marker in ("first", "second"):
                for record in ({"role": "user", "content": "prompt"},
                               {"role": "assistant", "content": "", "reasoning_content": "think",
                                "tool_calls": [{"id": f"c_{marker}", "type": "function", "function": {"name": "exec", "arguments": json.dumps({"command": f"echo {marker}-call-marker"})}}]},
                               {"role": "tool", "tool_call_id": f"c_{marker}", "name": "exec", "content": f"{marker}-call-marker\nExit code: 0"},
                               {"role": "assistant", "content": f"{marker}-call-marker"}):
                    db.execute("INSERT INTO messages VALUES (?,?,?)", (key, position, json.dumps(record)))
                    position += 1
            db.commit()
        self.check("nanobot", key, usage=False)

    def test_muse_also_in_a_launchers_private_data_folder(self):
        session = "01a1f953"
        rows = []
        for marker, at in (("first", T0), ("second", T0 + 10.4)):
            run = lambda event, time: {"record_type": "event", "payload_type": "runtime.session", "recorded_at": int(time * 1e6),
                                       "payload": {"kind": "run", "event": event}}
            rows += [
                run({"kind": "started", "prompt": "prompt"}, at + 0.1),
                run({"kind": "reasoning_summary_committed", "message_id": f"r-{marker}", "text": "think"}, at + 0.5),
                run({"kind": "assistant_tool_calls_committed", "tool_calls": [{"call_id": f"ps_{marker}", "name": "powershell",
                                                                                 "args": json.dumps({"command": f"echo {marker}-call-marker"})}]}, at + 0.6),
                run({"kind": "tool_result_batch_committed", "results": [{"tool_call_id": f"ps_{marker}", "text": f"{marker}-call-marker"}]}, at + 0.7),
                run({"kind": "model_completed", "usage": {"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 80}}, at + 0.8),
                run({"kind": "assistant_message_committed", "message_id": f"m-{marker}", "text": f"{marker}-call-marker"}, at + 9.8),
                run({"kind": "terminal", "terminal": "completed"}, at + 9.9)]
        # The first record of a session is a retained frame of child records.
        frame = {"retained_frame": "session_permission_transaction", "children": [{"child_index": 0, "record_json": json.dumps(rows[0])}]}
        folder = self.root / "records/native-profiles/muse-volcengine/data/muse/sessions/2026/11/20" / session
        jsonl(folder / "session.jsonl", [frame, *rows[1:]])
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "elsewhere")}):
            self.check("muse", session)

    def test_a_record_desk_cannot_read_shows_no_process_and_fails_nothing(self):
        session = "5b2752cd"
        (self.root / "copilot/session-state" / session).mkdir(parents=True)
        (self.root / "copilot/session-state" / session / "events.jsonl").write_text('{"type": "assistant.message", "timestamp": 12, "data": []}\nnot json\n', encoding="utf-8")
        page = native_calls.page("call_a", calls("copilot", session, self.root))
        self.assertTrue(page["available"])
        self.assertEqual([event["kind"] for event in page["events"]], ["turn"])
        missing = native_calls.page("call_a", calls("kimi", "session_none", self.root))
        self.assertFalse(missing["available"])
        self.assertIn("Kimi Code", missing["missing"])


if __name__ == "__main__":
    unittest.main()
