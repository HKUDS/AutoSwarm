import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from autoswarm.desk.native_history import conversation


class NativeHistoryTests(unittest.TestCase):
    def test_exact_binding_title_order_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = sqlite3.connect(root / "state_5.sqlite")
            db.execute("CREATE TABLE threads (id TEXT,title TEXT,name TEXT,cwd TEXT)")
            db.executemany("INSERT INTO threads VALUES (?,?,?,?)", [("bound", "first prompt", "任务主标题", temp), ("unrelated", "private", "not imported", temp)])
            db.commit(); db.close()
            db = sqlite3.connect(root / "thread_history_1.sqlite")
            db.execute("CREATE TABLE thread_turns (thread_id TEXT,status TEXT,rollout_ordinal INT)")
            db.execute("INSERT INTO thread_turns VALUES ('bound','completed',1)")
            db.execute("CREATE TABLE thread_items (thread_id TEXT,item_json TEXT,created_at_ms INT,item_type TEXT,rollout_ordinal INT)")
            for i, item in enumerate([{"type":"userMessage","content":[{"type":"text","text":"用户的需求"}]}, {"type":"agentMessage","text":"实际回复","phase":"final"}]):
                db.execute("INSERT INTO thread_items VALUES (?,?,?,?,?)", ("bound",json.dumps(item),i,item['type'],i))
            db.commit();db.close()
            before={p.name:p.read_bytes() for p in root.iterdir()}
            result=conversation({"host":"codex","session_id":"bound"},home=root)
            self.assertEqual(result["title"],"任务主标题")
            self.assertFalse(result["busy"])
            self.assertEqual([m["text"] for m in result["messages"]],["用户的需求","实际回复"])
            self.assertEqual(conversation({"host":"codex","session_id":"unknown"},home=root),{})
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir()})

    def test_structured_turns_follow_codex_grouping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = sqlite3.connect(root / "state_5.sqlite")
            db.execute("CREATE TABLE threads (id TEXT,title TEXT,name TEXT,cwd TEXT)")
            db.execute("INSERT INTO threads VALUES ('t','调研','调研轨迹',?)", (temp,))
            db.commit(); db.close()
            db = sqlite3.connect(root / "thread_history_1.sqlite")
            db.execute("CREATE TABLE thread_turns (thread_id TEXT,turn_id TEXT,rollout_ordinal INT,status TEXT,error_json TEXT,started_at INT,completed_at INT,duration_ms INT,first_user_item_id TEXT,final_agent_item_id TEXT)")
            db.execute("CREATE TABLE thread_items (thread_id TEXT,turn_id TEXT,item_id TEXT,rollout_ordinal INT,created_at_ms INT,item_json TEXT,item_type TEXT,updated_at_ordinal INT)")
            db.execute("INSERT INTO thread_turns VALUES ('t','turn1',1,'completed',NULL,1000,3000,2195000,'u1','f1')")
            long_answer = "结论" * 5000
            items = [
                ("u1", {"type": "userMessage", "content": [{"type": "text", "text": "查一下轨迹"}]}),
                ("c1", {"type": "agentMessage", "text": "先检查本地可用性。", "phase": "commentary"}),
                ("r1", {"type": "reasoning", "summary": ["比较两个版本"], "content": []}),
                ("x1", {"type": "commandExecution", "command": "pwsh -c autoswarm --version", "commandActions": [{"type": "unknown", "command": "autoswarm --version"}],
                        "status": "failed", "exitCode": 1, "aggregatedOutput": "拒绝访问"}),
                ("w1", {"type": "webSearch", "query": "terminal-bench trajectories", "results": [{"url": "https://example.org"}]}),
                ("e1", {"type": "fileChange", "status": "completed", "changes": [{"path": "out/a.json", "kind": {"type": "add"}, "diff": "+{}"}]}),
                ("i1", {"type": "userMessage", "content": [{"type": "text", "text": "AutoSwarm child result: done"}]}),
                ("f1", {"type": "agentMessage", "text": long_answer, "phase": "final_answer"}),
            ]
            for index, (item_id, item) in enumerate(items):
                item["id"] = item_id
                db.execute("INSERT INTO thread_items VALUES ('t','turn1',?,?,?,?,?,?)", (item_id, index, 1000 + index, json.dumps(item), item["type"], index))
            db.commit(); db.close()
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            summary = conversation({"host": "codex", "session_id": "t"}, home=root, detail=False)
            self.assertNotIn("turns", summary)
            self.assertTrue(summary["version"])
            result = conversation({"host": "codex", "session_id": "t"}, home=root)
            (turn,) = result["turns"]
            self.assertEqual(turn["user"]["text"], "查一下轨迹")
            self.assertEqual(turn["duration_ms"], 2195000)
            self.assertEqual(turn["final"]["text"], long_answer, "the final answer is never clipped to the commentary limit")
            kinds = [(i["kind"], i.get("tool")) for i in turn["items"]]
            self.assertEqual(kinds, [("message", None), ("thought", None), ("tool", "execute"), ("tool", "search"), ("tool", "edit"), ("notice", None)])
            command = turn["items"][2]
            self.assertEqual((command["title"], command["status"], command["exit"]), ("autoswarm --version", "failed", 1))
            self.assertEqual(turn["items"][4]["changes"][0]["path"], "out/a.json")
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})

    def test_missing_or_unknown_native_schema_is_optional(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertEqual(conversation({"host":"codex","session_id":"missing"},home=root),{})
            (root/'state_99.sqlite').write_text('not sqlite')
            self.assertEqual(conversation({"host":"codex","session_id":"missing"},home=root),{})

