"""A stand-in for websockets.sync.client.connect that plays JiuwenSwarm's Gateway `/tui` route in-process.

JIUWEN_FIXTURE_STATE/gateway.jsonl records every frame the bridge sends; JIUWEN_FIXTURE_GATEWAY=down refuses the
connection. Prompts: "ask" pauses for a permission card (resumed by the answer); "hold" waits for chat.interrupt;
"idle" ends with a leader reply but no closing status; anything else runs a team round with one member.
"""
import json
import os
import queue
import threading
import time
from pathlib import Path

from ..exceptions import ConnectionClosed


def connect(url, **options):
    if os.environ.get("JIUWEN_FIXTURE_GATEWAY") == "down":
        raise ConnectionRefusedError("[WinError 10061] fixture gateway is down")
    return Connection(url, options)


class Connection:
    def __init__(self, url, options):
        self.url, self.options = url, options
        self.frames = queue.Queue()
        self.closed = False
        self.session = None
        self.trace({"connect": url, "proxy": options.get("proxy", "unset")})
        self.push("connection.ack", {})

    def trace(self, value):
        state = Path(os.environ["JIUWEN_FIXTURE_STATE"])
        state.mkdir(parents=True, exist_ok=True)
        with (state / "gateway.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")

    def push(self, event, payload):
        self.frames.put(json.dumps({"type": "event", "event": event, "payload": payload}, ensure_ascii=False))

    def chat(self, inner, role="leader", member=None, frame="chat.final", **fields):
        payload = {"event_type": inner, "session_id": self.session, "role": role, **({"member_id": member} if member else {}), **fields}
        self.push(frame, payload)

    def send(self, text):
        frame = json.loads(text)
        self.trace({"sent": frame})
        params = frame.get("params") or {}
        if frame.get("method") == "chat.interrupt":
            self.chat("chat.processing_status", frame="chat.processing_status", is_processing=False, is_complete=False)
        elif frame.get("method") == "chat.send" and params.get("source"):
            threading.Thread(target=self.resume, args=(params,), daemon=True).start()
        elif frame.get("method") == "chat.send":
            self.session = params["session_id"]
            threading.Thread(target=self.round, args=(params,), daemon=True).start()

    def round(self, params):
        text = params["content"].strip().splitlines()[0]
        self.chat("chat.processing_status", frame="chat.processing_status", is_processing=True)
        self.chat("team.runtime_ready")
        self.chat("chat.tool_call", frame="chat.tool_call", tool_call={"name": "build_team", "arguments": "{}", "tool_call_id": "lead-1"})
        self.chat("chat.tool_result", frame="chat.tool_result", tool_call_id="lead-1", success=True, rendered_result="Team created")
        if text.startswith("hold"):
            return
        if text.startswith("ask"):
            question = {"question": "bash（当前模式默认需确认）", "card_id": "card-1", "tool_payload": {"command": "echo from-team"},
                        "options": [{"value": "allow_once", "label": "Allow once"}, {"value": "reject", "label": "Reject"}]}
            self.push("chat.ask_user_question", {"session_id": self.session, "request_id": "req-1", "source": "permission_interrupt", "questions": [question]})
            self.chat("chat.processing_status", frame="chat.processing_status", is_processing=False, is_complete=False)  # The paused stream ends.
            return
        self.chat("team.task", frame="team.task", **{"event": {"type": "team.task.created", "task_id": "task-1", "status": "pending", "title": "Echo"}})
        self.chat("chat.tool_call", role="teammate", member="member-a", frame="chat.tool_call",
                  tool_call={"name": "bash", "arguments": json.dumps({"command": "echo team"}), "tool_call_id": "mem-1"})
        self.chat("chat.tool_result", role="teammate", member="member-a", frame="chat.tool_result", tool_call_id="mem-1", success=True, rendered_result="team")
        self.chat("chat.final", role="teammate", member="member-a", content="member-a ran echo team")
        self.chat("team.task", frame="team.task", **{"event": {"type": "team.task.completed", "task_id": "task-1", "status": "completed"}})
        self.chat("chat.delta", frame="chat.delta", content="team: ")
        self.chat("chat.delta", frame="chat.delta", content=text)
        self.chat("chat.final", content="team: " + text)
        if not text.startswith("idle"):
            time.sleep(0.2)
            self.chat("chat.processing_status", frame="chat.processing_status", is_processing=False, is_complete=True)

    def resume(self, params):
        allowed = params["answers"][0]["selected_options"] == ["allow_once"] and params["answers"][0].get("card_id") == "card-1"
        self.chat("chat.processing_status", frame="chat.processing_status", is_processing=True)
        self.chat("chat.final", content="approved" if allowed else "rejected")
        self.chat("chat.processing_status", frame="chat.processing_status", is_processing=False, is_complete=True)

    def recv(self, timeout=None):
        if self.closed:
            raise ConnectionClosed(None, None)
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError() from None

    def close(self):
        self.closed = True
        self.trace({"closed": True})
