"""A stand-in for `jiuwenswarm-process` (its run-jsonl and query-json machine protocol) for AutoSwarm's tests.

State lives in JIUWEN_FIXTURE_STATE: sessions/<id>.json and trace.jsonl (every request document and control line).
Prompts: "ask:<file>" asks permission to write <file> in the cwd; "question" asks one single-choice question;
"hold" waits for cancel; "fail" fails the run; "recall" replies with the session's first input. Anything else runs
one echo tool and replies "jiuwen: <first line>".
"""
import json
import os
import sys
import uuid
from pathlib import Path

state = Path(os.environ["JIUWEN_FIXTURE_STATE"])
(state / "sessions").mkdir(parents=True, exist_ok=True)
sequence = 0


def trace(value):
    with (state / "trace.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def out(record):
    global sequence
    sys.stdout.buffer.write((json.dumps({"schema_version": "0.1", "sequence": sequence, **record}, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    sequence += 1


def control():
    line = sys.stdin.buffer.readline()
    if not line:
        return {"type": "eof"}
    value = json.loads(line)
    trace({"control": value})
    return value


if sys.argv[1:] == ["--query-json", "-"]:
    query = json.loads(sys.stdin.buffer.read())
    trace({"query": query})
    data = {"model.list": {"models": [{"selection_key": "fixture-model#0", "display_name": "Fixture Model", "model_name": "fixture-model", "is_default": True}],
                           "current_selection": "fixture-model#0"},
            "permission.get": {"scope": "host", "global": {"enabled": True, "tools": [{"name": "write_file", "level": "ask"}, {"name": "fixture_tool", "level": "ask"},
                                                                                  {"name": "read_file", "level": "allow"}]}}}.get(query["operation"])
    out({"type": "query_result", "request_id": query["request_id"], "session_id": None, "operation": query["operation"],
         "status": "completed", "exit_code": 0, "data": data, "error": None})
    sys.exit(0)

assert sys.argv[1:] == ["--run-jsonl"], sys.argv
run = json.loads(sys.stdin.buffer.readline())
trace({"run": run, "cwd": os.getcwd()})
request = run["request_id"]
session = run.get("session_id")
if session:
    path = state / "sessions" / (session + ".json")
    if not path.is_file():
        out({"type": "result", "request_id": request, "session_id": session, "status": "failed", "exit_code": 1, "output": None,
             "error": {"code": "SESSION_NOT_FOUND", "message": "No Process CLI session " + session}})
        sys.exit(1)
    history = json.loads(path.read_text(encoding="utf-8"))
else:
    session = "process_cli_fixture_" + uuid.uuid4().hex[:12]
    history = {"inputs": [], "mode": run.get("mode")}
text = run["input"]
history["inputs"].append(text)
(state / "sessions" / (session + ".json")).write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")


def event(payload):
    out({"type": "event", "request_id": request, "session_id": session, "event_type": payload["event_type"], "payload": payload})


def finish(output, status="completed", code=0, error=None):
    out({"type": "result", "request_id": request, "session_id": session, "status": status, "exit_code": code, "output": output,
         "usage": {"input_tokens": 10, "output_tokens": 2}, "error": error})
    sys.exit(code)


def say(content):
    event({"event_type": "chat.delta", "content": content})
    event({"event_type": "chat.final", "content": content})


def permission(tool_id, name, payload):
    question = {"question": name + "（当前模式默认需确认）", "header": "工具需要授权后才能使用",
                "options": [{"value": v, "label": v} for v in ("allow_once", "session_allow", "always_allow", "reject")], "tool_payload": payload}
    event({"event_type": "interaction.requested", "interaction_id": "interaction-" + tool_id, "kind": "permission",
           "interaction": {"event_type": "chat.ask_user_question", "request_id": tool_id, "questions": [question], "source": "permission_interrupt"}})
    return control()


event({"event_type": "run.started"})
first = text.strip().splitlines()[0] if text.strip() else ""
if first.startswith("hold"):
    event({"event_type": "chat.delta", "content": "holding"})
    while control().get("type") not in {"cancel", "eof"}:
        pass
    finish(None, "cancelled", 130, {"code": "CANCELLED", "message": "Command cancelled by its caller."})
elif first.startswith("fail"):
    finish(None, "failed", 1, {"code": "MODEL_ERROR", "message": "fixture failure"})
elif first.startswith("recall"):
    say("first: " + history["inputs"][0].strip().splitlines()[0])
    finish("first: " + history["inputs"][0].strip().splitlines()[0])
elif first.startswith("question"):
    question = {"question": "Which colour?", "options": [{"value": "red", "label": "Red"}, {"value": "blue", "label": "Blue"}]}
    event({"event_type": "interaction.requested", "interaction_id": "interaction-q", "kind": "question",
           "interaction": {"event_type": "chat.ask_user_question", "request_id": "ask-1", "questions": [question], "source": "ask_user_interrupt"}})
    answer = control()
    choice = answer["answers"][0]["selected_options"][0] if answer.get("type") == "answer" else None
    if choice is None:
        finish(None, "cancelled", 130, {"code": "CANCELLED", "message": "cancelled"})
    say("chose " + choice)
    finish("chose " + choice)
elif first.startswith("ask:"):
    name = first[4:].strip()
    tool_id = "call_write_" + uuid.uuid4().hex[:6]
    event({"event_type": "chat.tool_call", "tool_call": {"name": "write_file", "arguments": json.dumps({"file_path": name, "content": "written"}), "tool_call_id": tool_id}})
    allowed = (run.get("permissions") or {}).get("tools", {}).get("write_file") == "allow"
    if not allowed:
        answer = permission(tool_id, "write_file", {"file_path": name, "content": "written"})
        if answer.get("type") == "cancel":
            finish(None, "cancelled", 130, {"code": "CANCELLED", "message": "cancelled"})
        allowed = answer.get("type") == "answer" and answer["answers"][0]["selected_options"] == ["allow_once"]
        assert answer.get("type") != "answer" or answer["interaction_id"] == "interaction-" + tool_id and answer["session_id"] == session, answer
    if allowed:
        Path(name).write_text("written", encoding="utf-8")
    event({"event_type": "chat.tool_result", "tool_call_id": tool_id, "tool_name": "write_file", "success": allowed,
           "rendered_result": "wrote " + name if allowed else "rejected by the user"})
    say("allowed " + name if allowed else "not allowed")
    finish("allowed " + name if allowed else "not allowed")
else:
    event({"event_type": "chat.reasoning", "content": "thinking"})
    event({"event_type": "chat.tool_call", "tool_call": {"name": "bash", "arguments": json.dumps({"command": "echo fixture"}), "tool_call_id": "call_echo"}})
    event({"event_type": "chat.tool_result", "tool_call_id": "call_echo", "tool_name": "bash", "success": True, "rendered_result": "fixture"})
    say("jiuwen: " + first)
    finish("jiuwen: " + first)
