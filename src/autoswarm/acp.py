"""One ACP client shared by native agents and the local nanobot SDK shim."""
import os
import threading
import time

from .records import SwarmError

# Agents whose stdin must keep receiving input on Windows (see ACP.keepalive).
KEEPALIVE = {"hermes"} if os.name == "nt" else set()
KEEPALIVE_SECONDS = 1
KEEPALIVE_LINE = {"id": "autoswarm-keepalive", "result": None}


class ACP:
    def __init__(self, native):
        self.n = native
        self.accepting = False
        self.after_tool = False
        self.permissions = set()
        self.tool_calls = {}
        self.interactions = {}
        self.codebuddy_questions = {}
        self.kimi_questions = set()
        self.permission_keys = {}
        self.permission_lock = threading.RLock()
        self.interrupted_for_message = False
        self.cancelled_request = None
        self.retry_prompt = None
        self.prompt_text = None

    def start(self):
        if self.n.task["provider"] in KEEPALIVE:
            threading.Thread(target=self.keepalive, daemon=True).start()
        self.n.stage("handshake")
        capabilities = {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False, "elicitation": {"form": {}, "url": {}}}
        if self.n.task["provider"] == "cursor":
            capabilities["_meta"] = {"parameterizedModelPicker": True}
        self.n.request("initialize", {"protocolVersion": 1, "clientInfo": {"name": "autoswarm", "version": "2.0.0.dev0"}, "clientCapabilities": capabilities}, self.initialized)

    TOOL_FIELDS = ("title", "kind", "rawInput", "locations", "content")

    def remember_tool(self, update):
        """What a tool call has said about itself so far (tool_call, then its updates)."""
        known = self.tool_calls.setdefault(update["toolCallId"], {})
        for key in self.TOOL_FIELDS:
            if update.get(key) not in (None, "", [], {}):
                known[key] = update[key]
        if len(self.tool_calls) > 500:  # Only recent calls can still ask for permission.
            self.tool_calls.pop(next(iter(self.tool_calls)))

    def described(self, params):
        """A permission request may name its tool call by id only (DSH) or by a bare tool name
        (Kimi's "Bash"); what the call reported earlier fills in what the request leaves out, so
        whoever decides sees the command or file. The request's own fields win."""
        tool = params.get("toolCall")
        known = self.tool_calls.get(tool.get("toolCallId")) if isinstance(tool, dict) else None
        if not known:
            return params
        merged = {**tool, **{key: value for key, value in known.items() if tool.get(key) in (None, "", [], {})}}
        return {**params, "toolCall": merged}

    def keepalive(self):
        """Hermes on Windows reads its stdin pipe with a blocking read on one thread. A child it
        starts (git, a shell command) inherits that pipe, and on Windows the child's startup waits
        until the pending read returns, so without input Hermes stops at session/new or at its
        first tool. A reply to no request is dropped silently by its ACP library and lets the read
        return."""
        while self.n.process.poll() is None:
            time.sleep(KEEPALIVE_SECONDS)
            try:
                self.n.send(KEEPALIVE_LINE)
            except (OSError, ValueError, SwarmError):
                return

    def initialized(self, result):
        if result.get("protocolVersion") != 1:
            raise SwarmError("native_protocol_error", "Unsupported ACP protocol version")
        self.n.progress("handshake", native_capabilities=result.get("agentCapabilities", {}))
        session = self.n.task.get("native_session_id")
        params = {"cwd": self.n.task["workspace"], "mcpServers": []}
        method = "session/new"
        if session:
            capabilities = result.get("agentCapabilities", {})
            if capabilities.get("loadSession"):
                method = "session/load"
            elif "resume" in capabilities.get("sessionCapabilities", {}):
                method = "session/resume"
            else:
                raise SwarmError("session_unavailable", "This native ACP server advertises neither session/load nor session/resume")
            params["sessionId"] = session
        self.n.stage("session")
        self.n.request(method, params, self.started)

    def started(self, result):
        self.n.session(result.get("sessionId") or self.n.task.get("native_session_id"))
        if not self.n.output.session_id:
            raise SwarmError("native_protocol_error", "ACP server returned no native session ID")
        self.n.output.content = ""  # session/load may replay the old transcript.
        self.accepting = True
        self.modes = result.get("modes") or {}
        from .selection import configure_acp
        configure_acp(self.n, result, self.configured)

    def configured(self):
        # Desk's permission mode selects a native session mode (Cursor Plan / Agent).
        mode = self.n.task.get("native_options", {}).get("mode")
        if mode and self.modes.get("currentModeId") != mode:
            if not any(item.get("id") == mode for item in self.modes.get("availableModes", [])):
                raise SwarmError("unsupported_permission", f"{self.n.task['provider']} did not offer the {mode!r} session mode")
            self.modes = {**self.modes, "currentModeId": mode}
            self.n.request("session/set_mode", {"sessionId": self.n.output.session_id, "modeId": mode}, lambda _: self.configured())
            return
        self.n.stage("execution")
        self.n.continue_turn(self.n.initial_prompt())

    def prompt(self, text):
        self.prompt_text = text
        self.after_tool = False
        tap = getattr(self.n, "tap", None)
        if tap is not None:
            tap.mark("prompt")  # session/load replayed the old transcript before this point.
        self.n.request("session/prompt", {"sessionId": self.n.output.session_id, "prompt": [{"type": "text", "text": text}]}, self.completed)

    def poll(self):
        if self.retry_prompt is not None:
            text, self.retry_prompt = self.retry_prompt, None
            self.prompt(text)

    def interrupt(self):
        self.interrupted_for_message = True
        with self.permission_lock:
            self.n.send({"method": "session/cancel", "params": {"sessionId": self.n.output.session_id}})
            for request, (native_id, response) in list(self.interactions.items()):
                self.n.broker.withdraw(native_id, "interrupted_for_message")
                self.n.send({"id": request, "result": response})
            self.interactions.clear()
            self.kimi_questions.clear()
            for request in list(self.permissions):
                self.permissions.remove(request)
                self.n.broker.withdraw(self.permission_keys.pop(request, request), "interrupted_for_message")
                self.n.send({"id": request, "result": {"outcome": {"outcome": "cancelled"}}})

    def completed(self, result):
        reason = result.get("stopReason")
        request_id = result.get("_meta", {}).get("codebuddy.ai/requestId")
        if (self.cancelled_request and reason == "cancelled" and not result.get("userMessageId")
                and request_id == self.cancelled_request):
            # CodeBuddy's native cancellation barrier can reject a new prompt
            # before it enters history, echoing the previous request ID.
            # Yield to the runner and retry this unaccepted prompt, without
            # rerunning accepted work, adding a deadline, or resetting history.
            self.retry_prompt = self.prompt_text
            self.n.progress("execution", diagnostic="Waiting for native cancellation barrier to clear")
            return
        if self.interrupted_for_message and reason == "cancelled" and self.n.task["provider"] == "codebuddy":
            self.cancelled_request = request_id
        else:
            self.cancelled_request = None
        self.interrupted_for_message = False
        with self.permission_lock:
            self.permissions.clear()
            self.interactions.clear()
            self.permission_keys.clear()
            self.codebuddy_questions.clear()
            self.kimi_questions.clear()
        self.n.output.finish_reason = reason
        self.n.output.terminal = reason == "end_turn"
        self.n.output.cancelled = reason == "cancelled"
        if not self.n.output.terminal and not self.n.output.cancelled:
            detail = result.get("_meta", {}).get("codebuddy.ai/errorMessage")
            self.n.output.error = "Native ACP prompt stopped: " + str(reason) + (": " + str(detail) if detail else "")
        self.n.finish_turn()

    def codebuddy_question(self, tool, params, rpc_id=None):
        """Native web/TUI interruptions and permission RPCs share one question."""
        tool_id = tool["toolCallId"]
        key = "codebuddy:" + tool_id
        with self.permission_lock:
            state = self.codebuddy_questions.setdefault(tool_id, {"ids": set(), "settled": False})
            if rpc_id is not None:
                if state["settled"]:
                    self.n.send({"id": rpc_id, "result": {"outcome": {"outcome": "cancelled"}}})
                    return
                self.permissions.add(rpc_id)
                self.permission_keys[rpc_id] = key
                state["ids"].add(rpc_id)

        def answer(value):
            body = value["response"]
            result = self.n.command("_codebuddy.ai/resolveInterruption", {
                "sessionId": self.n.output.session_id, "toolCallId": tool_id,
                "decision": "deny" if body.get("action") == "cancel" else "allow",
                **({"answers": body["answers"]} if "answers" in body else {})})
            if result.get("resolved") is not True:
                raise SwarmError("native_answer_rejected", "CodeBuddy interruption is no longer answerable: " + str(result))
            with self.permission_lock:
                state["settled"] = True
                for request in state["ids"]:
                    self.permissions.discard(request)
                    self.permission_keys.pop(request, None)
                    # Local interruption won the native race; release its
                    # original RPC without granting a second tool execution.
                    self.n.send({"id": request, "result": {"outcome": {"outcome": "cancelled"}}})
                state["ids"].clear()
        self.n.broker.request(key, self.n.output.session_id, params, answer, interaction="codebuddy-question")

    def feed(self, event):
        method, params = event.get("method"), event.get("params", {})
        if method == "session/update":
            if params.get("sessionId") != self.n.output.session_id or not self.accepting:
                return
            update = params.get("update", {})
            meta = update.get("_meta", {})
            if update.get("sessionUpdate") in {"tool_call", "tool_call_update"} and update.get("toolCallId"):
                self.remember_tool(update)
            if (self.n.task["provider"] == "kimi" and update.get("sessionUpdate") in {"tool_call", "tool_call_update"}
                    and update.get("title") == "AskUserQuestion" and update.get("toolCallId")):
                self.kimi_questions.add(update["toolCallId"])
            if self.n.task["provider"] == "codebuddy":
                interruption = meta.get("codebuddy.ai/interruptionRequest")
                if isinstance(interruption, dict) and interruption.get("toolName") == "AskUserQuestion":
                    tool = {"toolCallId": interruption.get("toolCallId"), "rawInput": interruption.get("toolInput", {})}
                    if tool["toolCallId"] and isinstance(tool["rawInput"], dict) and isinstance(tool["rawInput"].get("questions"), list):
                        self.codebuddy_question(tool, {"sessionId": self.n.output.session_id, "toolCall": tool, "interruption": interruption})
                    else:
                        self.n.broker.unsupported(interruption.get("toolCallId"), params)
                if meta.get("codebuddy.ai/permissionResolved"):
                    tool_id = str(meta.get("codebuddy.ai/toolCallId"))
                    with self.permission_lock:
                        self.n.broker.withdraw("codebuddy:" + tool_id, "resolved_by_native")
                        state = self.codebuddy_questions.get(tool_id)
                        if state:
                            state["settled"] = True
                            for request in state["ids"]:
                                self.permissions.discard(request)
                                self.permission_keys.pop(request, None)
                            state["ids"].clear()
            if update.get("sessionUpdate") == "agent_message_chunk" and update.get("content", {}).get("type") == "text":
                if self.after_tool:
                    self.n.output.content = ""
                    self.after_tool = False
                self.n.output.content += update["content"].get("text", "")
            elif update.get("sessionUpdate") == "tool_call":
                self.after_tool = True
        elif method == "elicitation/complete":
            if not params.get("sessionId") or params["sessionId"] == self.n.output.session_id:
                native_id = "elicitation:" + str(params.get("elicitationId"))
                with self.permission_lock:
                    self.n.broker.withdraw(native_id, "resolved_by_native")
                    for request, (key, _) in list(self.interactions.items()):
                        if key == native_id:
                            self.interactions.pop(request, None)
        elif method in {"cursor/ask_question", "cursor/create_plan", "elicitation/create", "_codebuddy.ai/question"} and "id" in event:
            if params.get("sessionId") and params["sessionId"] != self.n.output.session_id:
                self.n.unknown(event)
                return
            kind = {"cursor/ask_question": "cursor-question", "cursor/create_plan": "cursor-plan",
                    "elicitation/create": "elicitation", "_codebuddy.ai/question": "codebuddy-question"}[method]
            native_id = "elicitation:" + str(params["elicitationId"]) if method == "elicitation/create" and params.get("mode") == "url" and params.get("elicitationId") else event["id"]
            def answer(value):
                with self.permission_lock:
                    if event["id"] not in self.interactions:
                        raise SwarmError("interaction_not_pending", "Native interaction was withdrawn")
                    response = value["response"]
                    if method == "_codebuddy.ai/question":
                        response = {"outcome": {"outcome": "cancelled"} if response.get("action") == "cancel" else {"outcome": "submitted", "data": {"answers": response["answers"]}}}
                    self.n.send({"id": event["id"], "result": response})
                    self.interactions.pop(event["id"], None)
            cancellation = ({"action": "cancel"} if kind == "elicitation" else
                            {"outcome": {"outcome": "cancelled"}} if method == "_codebuddy.ai/question" else {"outcome": "cancelled"})
            with self.permission_lock:
                self.interactions[event["id"]] = (native_id, cancellation)
                # Kimi's native AskUserQuestion shares the elicitation form
                # wire format. Only a correlated native question is eligible
                # for parent delegation; MCP forms and URL consent stay user-only.
                native_question = (self.n.task["provider"] == "kimi" and method == "elicitation/create"
                                   and params.get("mode", "form") == "form"
                                   and params.get("toolCallId") in self.kimi_questions)
                self.n.broker.request(native_id, self.n.output.session_id, params, answer,
                                      interaction=kind, user_required=kind == "elicitation" and not native_question)
        elif method == "session/request_permission":
            if params.get("sessionId") != self.n.output.session_id:
                self.n.unknown(event)
                return
            params = self.described(params)
            tool = params.get("toolCall", {})
            tool_name = tool.get("_meta", {}).get("codebuddy.ai/toolName")
            if self.n.task["provider"] == "kimi" and tool.get("title") == "AskUserQuestion":
                # Native fallback when the form RPC is unavailable. These
                # options are answers, never executable-tool permissions.
                def answer_question(value):
                    with self.permission_lock:
                        if event["id"] not in self.interactions:
                            raise SwarmError("interaction_not_pending", "Native question was withdrawn")
                        response = value["response"]
                        outcome = ({"outcome": "cancelled"} if response.get("action") == "cancel" else
                                   {"outcome": "selected", "optionId": response["optionId"]})
                        self.n.send({"id": event["id"], "result": {"outcome": outcome}})
                        self.interactions.pop(event["id"], None)
                with self.permission_lock:
                    self.interactions[event["id"]] = (event["id"], {"outcome": {"outcome": "cancelled"}})
                    self.n.broker.request(event["id"], self.n.output.session_id, params, answer_question,
                                          interaction="acp-question")
                return
            if self.n.task["provider"] == "codebuddy" and (tool_name == "AskUserQuestion" or tool.get("title") == "AskUserQuestion"):
                self.codebuddy_question(tool, params, event["id"])
                return
            options = params.get("options", [])

            def reply(value):
                with self.permission_lock:
                    if event["id"] not in self.permissions:
                        return
                    kind = "allow_once" if value["decision"] == "allow" else "reject_once"
                    choice = next((item for item in options if item.get("kind") == kind), None)
                    if choice:
                        outcome = {"outcome": "selected", "optionId": choice["optionId"]}
                    elif value["decision"] == "deny":
                        outcome = {"outcome": "cancelled"}
                    else:
                        raise SwarmError("unsupported_approval_scope", "Native ACP server offered no one-time approval; persistent grants require explicit native configuration")
                    self.n.send({"id": event["id"], "result": {"outcome": outcome}})
                    self.permissions.remove(event["id"])

            # ACP's allow_always may persist beyond this session. Never label
            # it as a session grant or silently broaden one-time permission.
            with self.permission_lock:
                self.permissions.add(event["id"])
                self.n.broker.request(event["id"], self.n.output.session_id, params, reply, scopes=["once"])
        elif "id" in event or (isinstance(method, str) and any(word in method.lower() for word in ("question", "elicitation", "userinput", "authurl"))):
            self.n.unknown(event)
