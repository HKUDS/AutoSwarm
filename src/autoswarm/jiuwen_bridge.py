"""Local ACP wire shim for JiuwenSwarm, run by the Python of the installed jiuwenswarm-process.

Single agent: every prompt is one `jiuwenswarm-process --run-jsonl`, JiuwenSwarm's machine
protocol (one run per process); the next prompt continues the session in a new process.
Team: prompts go to the running JiuwenSwarm service through its Gateway `/tui` WebSocket, as
`jiuwenswarm chat --mode team` does; the team lives in that service.

JiuwenSwarm owns configuration, models, tools, permissions and session histories. Its permission
cards and questions become ACP requests, answered by AutoSwarm's approvals. No AutoSwarm imports.
"""
from __future__ import annotations

import os
import sys
# Executing this file must not shadow stdlib modules or the installed JiuwenSwarm with sibling
# AutoSwarm modules, including from an installed Skill copy.
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _script_dir]

import argparse
import io
import json
import re
import subprocess
import threading
import time
import uuid

SCHEMA = "0.1"
OLDEST = (0, 2, 8)  # The first JiuwenSwarm with the machine protocol (--run-jsonl, --query-json).
SINGLE_MODES = ("agent.code.normal", "agent.code.plan", "agent.work.normal", "agent.work.plan")
TEAM_MODES = ("team.code.normal", "team.code.plan", "team.work.normal", "team.work.plan", "team", "code.team")
ALIASES = {"agent": "agent.code.normal", "code": "agent.code.normal", "work": "agent.work.normal",
           "plan": "agent.code.plan", "team": "team.code.normal"}
# A new single-agent session gets JiuwenSwarm's own ID when its first run starts.
PENDING = "autoswarm-new-"
TEAM = "autoswarm-team-"
# Tools a JiuwenSwarm config commonly asks for; Full Access reads the configured list when it can.
GATED = ("bash", "powershell", "create_terminal", "mcp_exec_command", "write", "write_file", "edit_file", "search_replace", "acp_chat")
EXECUTE = {"bash", "powershell", "create_terminal", "mcp_exec_command"}
EDIT = {"write", "write_file", "edit_file", "search_replace"}
INTERRUPT_SOURCES = {"confirm_interrupt", "permission_interrupt", "ask_user_interrupt", "evolution_interrupt"}
OPEN_TASK = {"pending", "claimed", "in_progress"}
TEAM_IDLE = 5.0  # A leader reply with no open team task ends the round (as `jiuwenswarm chat` does).
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def version():
    from importlib.metadata import PackageNotFoundError, version as installed
    for name in ("workswarm", "jiuwenswarm"):  # Its PyPI package was renamed from jiuwenswarm.
        try:
            return installed(name)
        except PackageNotFoundError:
            continue
    return "unknown"


def outdated():
    """Why this JiuwenSwarm cannot run AutoSwarm's calls, if it is too old."""
    found = version()
    parts = tuple(int(n) for n in re.findall(r"\d+", found)[:3])
    if parts and parts < OLDEST:
        return (f"JiuwenSwarm {found} is older than {'.'.join(map(str, OLDEST))}, the first version with the machine protocol "
                "AutoSwarm uses; update it (for example: uv tool upgrade workswarm)")
    return None


def resolve_mode(value):
    mode = ALIASES.get((value or "agent").strip().lower(), (value or "").strip().lower())
    if mode not in SINGLE_MODES + TEAM_MODES:
        raise ValueError("Unknown JiuwenSwarm mode: " + str(value) + "; use agent, team or one of " + ", ".join(SINGLE_MODES + TEAM_MODES))
    return mode


def is_team(session):
    return not (session.startswith(PENDING) or session.startswith("process_cli_"))


def arguments(raw):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {"arguments": raw}
    return value if isinstance(value, dict) else {"arguments": value}


def tool_title(name, args):
    for key in ("command", "cmd", "file_path", "path", "query", "url"):
        if isinstance(args.get(key), str) and args[key].strip():
            return args[key].strip() if key in ("command", "cmd") else f"{name} {args[key].strip()}"
    return name or "tool"


def tool_kind(name):
    return "execute" if name in EXECUTE else "edit" if name in EDIT else "read" if name and name.startswith(("read", "view", "list", "search", "grep", "glob")) else "other"


def card_tool(question):
    """A permission card's question starts with the tool's name ("write_file（当前模式默认需确认）…")."""
    first = str(question.get("question") or "").replace("（", " ").replace("(", " ").split()
    return first[0] if first else str(question.get("header") or "JiuwenSwarm tool")


class Rendering:
    """Turn JiuwenSwarm's chat events into ACP session updates (a team member's work is labelled)."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.streamed = ""  # Text since the last tool call, which AutoSwarm takes as the reply.

    def feed(self, payload, member=None):
        kind = payload.get("event_type")
        update = self.bridge.update
        if kind == "chat.delta" and not member:
            text = payload.get("content") or ""
            if text:
                self.streamed += text
                update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})
        elif kind == "chat.reasoning" and not member:
            text = payload.get("content") or payload.get("reasoning") or ""
            if text:
                update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": text}})
        elif kind == "chat.tool_call":
            call = payload.get("tool_call") or {}
            name, args = call.get("name") or payload.get("tool_name") or "tool", arguments(call.get("arguments"))
            title = tool_title(name, args)
            self.bridge.tools[str(call.get("tool_call_id"))] = name  # A permission card names it by this ID.
            update({"sessionUpdate": "tool_call", "toolCallId": str(call.get("tool_call_id") or uuid.uuid4().hex),
                    "title": f"{member} · {title}" if member else title, "kind": tool_kind(name), "status": "in_progress", "rawInput": args})
            self.streamed = ""
        elif kind == "chat.tool_result":
            text = payload.get("rendered_result") or payload.get("result") or ""
            update({"sessionUpdate": "tool_call_update", "toolCallId": str(payload.get("tool_call_id")),
                    "status": "failed" if payload.get("success") is False else "completed",
                    **({"content": [{"type": "content", "content": {"type": "text", "text": str(text)}}]} if text else {})})
        elif kind == "chat.final":
            self.reply(payload.get("content") or payload.get("text") or "", member)

    def reply(self, text, member=None):
        if member:
            if text.strip():
                self.bridge.update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": f"{member}: {text.strip()}\n"}})
        elif text and text.startswith(self.streamed):
            # Deltas already showed the start of it; the final reply carries the rest.
            if text[len(self.streamed):]:
                self.bridge.update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text[len(self.streamed):]}})
            self.streamed = text


class Turn:
    def __init__(self, bridge, text):
        self.bridge, self.text = bridge, text
        self.cancelled = False
        self.failure = None  # A question AutoSwarm cannot relay ends the turn with this reason.
        self.lock = threading.Lock()

    def relay(self, payload, deliver):
        """Ask AutoSwarm about one permission card or question; deliver(question, option value) answers it.

        A declined card gets its reject option; one without a reject option cancels the turn."""
        questions = [q for q in payload.get("questions") or [] if isinstance(q, dict)]
        if len(questions) != 1 or not questions[0].get("options"):
            self.failure = "JiuwenSwarm asked something AutoSwarm cannot relay (free text or several questions)"
            self.cancel()
            return False
        question = questions[0]
        options = [str(o["value"]) for o in question["options"] if isinstance(o, dict) and o.get("value") is not None]
        labels = {str(o["value"]): str(o.get("label") or o["value"]) for o in question["options"] if isinstance(o, dict) and o.get("value") is not None}
        rejecting = next((v for v in options if "reject" in v or "deny" in v), None)
        tool_id = str(payload.get("request_id") or uuid.uuid4().hex)
        if payload.get("kind") == "permission" or payload.get("source") == "permission_interrupt":
            # A grant written to disk for every session (always_allow) is not offered.
            kinds = {v: "reject_once" if v == rejecting else "allow_once" if "once" in v else "allow_always" if "session" in v else None for v in options}
            offered = [{"optionId": v, "name": labels[v], "kind": kinds[v]} for v in options if kinds[v]]
            detail = question.get("tool_payload") if isinstance(question.get("tool_payload"), dict) else {}
            name = self.bridge.tools.get(tool_id) or card_tool(question)
            tool = {"toolCallId": tool_id, "title": tool_title(name, detail), "rawInput": detail,
                    "kind": "edit" if "file_path" in detail else "execute" if "command" in detail else "other"}
        else:
            offered = [{"optionId": v, "name": labels[v], "kind": "allow_once"} for v in options]
            tool = {"toolCallId": tool_id, "title": "AskUserQuestion", "rawInput": {"questions": [question]}}

        def answered(result):
            outcome = (result or {}).get("outcome") or {}
            value = outcome.get("optionId") if outcome.get("outcome") == "selected" else rejecting
            if value is None:
                self.cancel()
            else:
                deliver(question, value)

        self.bridge.ask("session/request_permission", {"sessionId": self.bridge.session, "toolCall": tool, "options": offered}, answered)
        return True

    def ended(self, reason):
        if self.failure:
            raise RuntimeError(self.failure)
        return reason


class ProcessTurn(Turn):
    """One prompt as one `jiuwenswarm-process --run-jsonl`."""

    def __init__(self, bridge, text):
        super().__init__(bridge, text)
        self.request_id = "autoswarm-" + uuid.uuid4().hex[:12]
        self.child = None

    def send(self, value):
        with self.lock:
            if self.child and self.child.poll() is None and not self.child.stdin.closed:
                try:
                    self.child.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.child.stdin.flush()
                except OSError:
                    pass

    def cancel(self):
        self.cancelled = True
        self.send({"schema_version": SCHEMA, "type": "cancel", "request_id": self.request_id})
        child = self.child
        if child:
            # JiuwenSwarm cleans up after a cancel; one that does not within 15 s is stopped.
            timer = threading.Timer(15, lambda: child.poll() is None and child.kill())
            timer.daemon = True
            timer.start()

    def run(self):
        bridge, args = self.bridge, self.bridge.args
        document = {"schema_version": SCHEMA, "type": "run", "request_id": self.request_id, "input": self.text,
                    "workspace": {"cwd": bridge.cwd, "project_dir": bridge.cwd, "trusted_dirs": [bridge.cwd]}}
        if bridge.session.startswith(PENDING):
            document["mode"] = bridge.mode if bridge.mode in SINGLE_MODES else "agent.code.normal"
        else:
            document["session_id"] = bridge.session
        if args.model:
            document["model"] = args.model
        if args.permission == "full-access":
            document["permissions"] = {"tools": {name: "allow" for name in bridge.gated()}}
        self.child = subprocess.Popen([*args.process, "--run-jsonl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, cwd=bridge.cwd, creationflags=NO_WINDOW)
        errors = []
        threading.Thread(target=lambda: errors.extend(self.child.stderr.read().decode("utf-8", "replace").splitlines()[-20:]), daemon=True).start()
        self.send(document)
        if self.cancelled:
            self.cancel()
        rendering, result = Rendering(bridge), None
        for raw in self.child.stdout:
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            session = record.get("session_id")
            if session and session != bridge.session:
                bridge.adopt(session)
            if record.get("type") == "result":
                result = record
                break
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            if payload.get("event_type") != "interaction.requested":
                rendering.feed(payload)
                continue

            def deliver(question, value, identifier=payload.get("interaction_id"), session=session):
                self.send({"schema_version": SCHEMA, "type": "answer", "request_id": self.request_id, "session_id": session, "interaction_id": identifier,
                           "answers": [{"question": question.get("question", ""), "selected_options": [value], "custom_input": ""}]})
            self.relay({**(payload.get("interaction") or {}), "kind": payload.get("kind")}, deliver)
        self.child.wait()
        if result is None:
            raise RuntimeError(outdated() or "JiuwenSwarm ended without a result" + (": " + " | ".join(errors[-3:]) if errors else ""))
        status, error = result.get("status"), result.get("error") or {}
        if status == "completed":
            if result.get("output") and not rendering.streamed:
                rendering.reply(result["output"])
            return self.ended("end_turn")
        if status == "cancelled" or error.get("code") in {"CANCELLED", "INPUT_CLOSED"}:
            return self.ended("cancelled")
        raise RuntimeError(f"JiuwenSwarm run {status}: {error.get('code') or ''} {error.get('message') or ''}".strip())


class GatewayTurn(Turn):
    """One prompt to a JiuwenSwarm team through the running service's Gateway."""

    def __init__(self, bridge, text):
        super().__init__(bridge, text)
        self.socket = None
        # A card pauses the stream, which then ends; only the stream an answer resumes (it reports
        # processing again) can end the turn. Unanswered cards and that wait both hold the turn open.
        self.unanswered = 0
        self.resumed = True

    def send(self, method, params, stream=False):
        frame = {"type": "req", "id": method.split(".")[-1] + "-" + uuid.uuid4().hex[:12], "method": method, "is_stream": stream, "params": params}
        with self.lock:
            if self.socket is not None:
                self.socket.send(json.dumps(frame, ensure_ascii=False))

    def cancel(self):
        self.cancelled = True
        try:
            self.send("chat.interrupt", {"session_id": self.bridge.session, "intent": "cancel", "mode": self.bridge.mode})
        except Exception:
            pass

    def run(self):
        bridge = self.bridge
        if bridge.args.model:
            raise RuntimeError("A JiuwenSwarm team uses the models configured in JiuwenSwarm; omit --model in team mode")
        try:
            from websockets.sync.client import connect
        except ImportError as error:
            raise RuntimeError("JiuwenSwarm's Python has no websockets client for team mode") from error
        url = bridge.args.gateway_url or f"ws://{os.environ.get('GATEWAY_HOST', '127.0.0.1')}:{os.environ.get('GATEWAY_PORT', '19001')}/tui"
        try:
            options = {"open_timeout": 10, "max_size": None, "ping_interval": 20, "ping_timeout": 60}
            try:
                # The local service is never reached through a system proxy (websockets 15 follows HTTP(S)_PROXY).
                self.socket = connect(url, proxy=None, **options)
            except TypeError:
                self.socket = connect(url, **options)  # websockets before 15 has no proxy support at all.
            ack = json.loads(self.socket.recv(timeout=30))
        except Exception as error:
            raise RuntimeError(f"JiuwenSwarm team mode runs in the JiuwenSwarm service, which is not reachable at {url}. "
                               f"Start it (jiuwenswarm-start app, or the JiuwenSwarm desktop app) and try again. ({type(error).__name__}: {error})") from None
        try:
            if ack.get("event") != "connection.ack":
                raise RuntimeError("JiuwenSwarm's Gateway did not acknowledge the connection")
            self.send("chat.send", {"session_id": bridge.session, "content": self.text, "query": self.text, "mode": bridge.mode,
                                    "cwd": bridge.cwd, "project_dir": bridge.cwd, "trusted_dirs": [bridge.cwd], "supports_user_interaction": True,
                                    "agent_ref": {"mode": bridge.mode, "id": "default"}}, stream=True)
            if self.cancelled:
                self.cancel()
            return self.listen()
        finally:
            with self.lock:
                socket, self.socket = self.socket, None
            try:
                socket.close()
            except Exception:
                pass

    def answer(self, source, request):
        def deliver(question, value):
            self.unanswered -= 1
            entry = {"selected_options": [value], "custom_input": ""}
            if source == "permission_interrupt" and question.get("card_id"):
                entry["card_id"] = question["card_id"]
            if source in INTERRUPT_SOURCES and request:
                self.resumed = False
                self.send("chat.send", {"session_id": self.bridge.session, "query": "", "request_id": request, "answers": [entry],
                                        "source": source, "mode": self.bridge.mode, "supports_user_interaction": True}, stream=True)
            else:
                self.send("chat.user_answer", {"session_id": self.bridge.session, "answers": [entry], "request_id": request})
        return deliver

    def listen(self):
        from websockets.exceptions import ConnectionClosed
        bridge = self.bridge
        rendering = Rendering(bridge)
        tasks, leader_final, last = {}, None, time.monotonic()
        while True:
            try:
                raw = self.socket.recv(timeout=1.0)
            except ConnectionClosed as error:
                raise RuntimeError(f"JiuwenSwarm's Gateway closed the connection ({error})") from None
            except TimeoutError:
                if self.cancelled and time.monotonic() - last > 10:
                    return self.ended("cancelled")
                if (leader_final and not self.unanswered and not OPEN_TASK & set(tasks.values())
                        and time.monotonic() - leader_final > TEAM_IDLE):
                    return self.ended("end_turn")
                continue
            last = time.monotonic()
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            event = frame.get("event") or ""
            payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
            if payload.get("session_id") not in (None, bridge.session):
                continue
            inner = payload.get("event_type") or event
            member = (payload.get("member_id") or payload.get("member_name") or "teammate") if payload.get("role") == "teammate" else None
            if event == "chat.error" or inner in {"chat.error", "team.error"}:
                raise RuntimeError("JiuwenSwarm team: " + str(payload.get("error") or payload.get("message") or payload.get("content") or inner))
            if event in {"chat.ask_user_question", "plan.approval_required"}:
                self.unanswered += 1
                if not self.relay(payload, self.answer(payload.get("source", ""), payload.get("request_id", ""))):
                    self.unanswered -= 1
            elif inner == "team.task":
                task = payload.get("event") if isinstance(payload.get("event"), dict) else {}
                if task.get("task_id"):
                    tasks[task["task_id"]] = task.get("status") or ""
                    if task.get("type") in {"team.task.created", "team.task.completed", "team.task.failed"}:
                        title = f" — {task['title']}" if task.get("title") and task.get("type") == "team.task.created" else ""
                        bridge.update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": f"Team task {task['task_id']}: {task.get('status')}{title}\n"}})
            elif inner == "chat.processing_status":
                if payload.get("is_processing") is True:
                    self.resumed = True
                elif payload.get("is_processing") is False and (self.cancelled or (not self.unanswered and self.resumed)):
                    return self.ended("cancelled" if self.cancelled else "end_turn")
            else:
                if not member and inner in {"chat.final", "chat.delta", "chat.tool_call", "chat.tool_result"}:
                    leader_final = time.monotonic() if inner == "chat.final" else None
                rendering.feed(payload, member=member)


class Bridge:
    def __init__(self, args, output):
        self.args, self.output = args, output
        self.mode = resolve_mode(args.mode)
        self.session = None
        self.cwd = None
        self.turn = None
        self.sequence = 0
        self.pending = {}
        self.write_lock = threading.Lock()
        self._gated = None
        self.tools = {}  # tool_call_id -> tool name, for permission cards

    def emit(self, value):
        with self.write_lock:
            self.output.write(json.dumps({"jsonrpc": "2.0", **value}, ensure_ascii=False) + "\n")
            self.output.flush()

    def update(self, update):
        self.emit({"method": "session/update", "params": {"sessionId": self.session, "update": update}})

    def ask(self, method, params, callback):
        with self.write_lock:
            self.sequence += 1
            request_id = "jiuwen-" + str(self.sequence)
            self.pending[request_id] = callback
        self.emit({"id": request_id, "method": method, "params": params})

    def adopt(self, session):
        """JiuwenSwarm names a new session when its first run starts; AutoSwarm keeps that name."""
        previous, self.session = self.session, session
        self.emit({"method": "session/update", "params": {"sessionId": previous, "update": {
            "sessionUpdate": "session_info_update", "_meta": {"autoswarm/nativeSessionId": session}}}})

    def gated(self):
        """The tools JiuwenSwarm's own configuration asks for (Full Access allows them for this run)."""
        if self._gated is None:
            names = set(GATED)
            try:
                request = {"schema_version": SCHEMA, "type": "query", "request_id": "autoswarm-permissions", "operation": "permission.get", "timeout_seconds": 60}
                found = subprocess.run([*self.args.process, "--query-json", "-"], input=json.dumps(request).encode(), capture_output=True,
                                       timeout=120, cwd=self.cwd, creationflags=NO_WINDOW)
                data = json.loads(found.stdout.decode("utf-8", "replace").strip().splitlines()[-1]).get("data") or {}
                names |= {t["name"] for t in (data.get("global") or {}).get("tools") or [] if isinstance(t, dict) and t.get("level") == "ask" and t.get("name")}
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass
            self._gated = sorted(names)
        return self._gated

    def prompt(self, request, turn):
        try:
            self.emit({"id": request["id"], "result": {"stopReason": turn.run()}})
        except Exception as error:
            self.emit({"id": request["id"], "error": {"code": -32603, "message": str(error)}})
        finally:
            self.turn = None

    def handle(self, request):
        method, params = request.get("method"), request.get("params") or {}
        if method is None and "id" in request:
            callback = self.pending.pop(request["id"], None)
            if callback:
                callback(request.get("result") if "result" in request else None)
            return
        if method == "initialize":
            result = {"protocolVersion": 1, "agentInfo": {"name": "autoswarm-jiuwen-runtime", "version": version()},
                      "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": False}}}
        elif method in {"session/new", "session/load"}:
            if self.session:
                raise ValueError("This JiuwenSwarm runtime already owns a session")
            self.cwd = os.path.abspath(params.get("cwd") or os.getcwd())
            self.session = str(params["sessionId"]) if method == "session/load" else (TEAM if self.mode in TEAM_MODES else PENDING) + uuid.uuid4().hex
            if is_team(self.session) and self.mode not in TEAM_MODES:
                self.mode = "team.code.normal"  # A team session continues as a team.
            result = {"sessionId": self.session}
        elif method == "session/prompt":
            if params.get("sessionId") != self.session:
                raise ValueError("Prompt belongs to another JiuwenSwarm session")
            if self.turn is not None:
                raise ValueError("The JiuwenSwarm session is busy")
            text = "\n".join(v.get("text", "") for v in params.get("prompt") or [] if v.get("type") == "text")
            if not text.strip():
                raise ValueError("Provide a text prompt")
            self.turn = (GatewayTurn if is_team(self.session) else ProcessTurn)(self, text)
            threading.Thread(target=self.prompt, args=(request, self.turn), daemon=True).start()
            return
        elif method == "session/cancel":
            if self.turn is not None:
                self.turn.cancel()
            return
        else:
            if "id" in request:
                self.emit({"id": request["id"], "error": {"code": -32601, "message": "Unsupported JiuwenSwarm runtime method: " + str(method)}})
            return
        self.emit({"id": request["id"], "result": result})

    def run(self, reader):
        while line := reader.readline():
            request = None
            try:
                request = json.loads(line)
                self.handle(request)
            except Exception as error:
                if isinstance(request, dict) and "id" in request and request.get("method"):
                    self.emit({"id": request["id"], "error": {"code": -32602, "message": str(error)}})
        if self.turn is not None:
            self.turn.cancel()


def private_wire():
    """Move the ACP wire to descriptors child processes do not inherit (dup'd ones are not, PEP 446)."""
    wire_in, wire_out = os.dup(0), os.dup(1)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.dup2(2, 1)
    reader = io.TextIOWrapper(io.BufferedReader(io.FileIO(wire_in, "rb")), encoding="utf-8")
    writer = io.TextIOWrapper(io.BufferedWriter(io.FileIO(wire_out, "wb")), encoding="utf-8", newline="\n", line_buffering=True)
    return reader, writer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process", action="append", required=True, help="The jiuwenswarm-process command (repeat for each argument)")
    parser.add_argument("--mode", default="agent", help="agent (single agent, default), team, or a JiuwenSwarm mode such as agent.work.normal or team.code.normal")
    parser.add_argument("--model", help="A model from JiuwenSwarm's own catalog (single agent)")
    parser.add_argument("--permission", choices=("full-access",), help="Allow, for this run, the tools JiuwenSwarm's config asks for")
    parser.add_argument("--gateway-url", help="The JiuwenSwarm Gateway's /tui WebSocket (team mode)")
    parser.add_argument("--version", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.version:
        # AutoSwarm's agent check: a JiuwenSwarm too old for the machine protocol is not available.
        problem = outdated()
        print(problem or "JiuwenSwarm " + version(), file=sys.stderr if problem else sys.stdout)
        raise SystemExit(1 if problem else 0)
    resolve_mode(args.mode)
    reader, output = private_wire()
    sys.stdout = sys.stderr  # Nothing may corrupt the wire.
    Bridge(args, output).run(reader)


if __name__ == "__main__":
    main()
