"""One ACP client shared by native agents and the local nanobot SDK shim."""
import threading

from .records import SwarmError


class ACP:
    def __init__(self, native):
        self.n = native
        self.accepting = False
        self.after_tool = False
        self.permissions = set()
        self.interactions = {}
        self.codebuddy_questions = {}
        self.permission_keys = {}
        self.permission_lock = threading.RLock()
        self.interrupted_for_message = False
        self.cancelled_request = None
        self.retry_prompt = None
        self.prompt_text = None

    def start(self):
        self.n.stage("handshake")
        capabilities = {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False, "elicitation": {"form": {}, "url": {}}}
        if self.n.task["provider"] == "cursor":
            capabilities["_meta"] = {"parameterizedModelPicker": True}
        self.n.request("initialize", {"protocolVersion": 1, "clientInfo": {"name": "autoswarm", "version": "2.0.0.dev0"}, "clientCapabilities": capabilities}, self.initialized)

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
        from .selection import configure_acp
        configure_acp(self.n, result, self.configured)

    def configured(self):
        self.n.stage("execution")
        self.n.continue_turn(self.n.initial_prompt())

    def prompt(self, text):
        self.prompt_text = text
        self.after_tool = False
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
                self.n.broker.request(native_id, self.n.output.session_id, params, answer,
                                      interaction=kind, user_required=kind == "elicitation")
        elif method == "session/request_permission":
            if params.get("sessionId") != self.n.output.session_id:
                self.n.unknown(event)
                return
            tool = params.get("toolCall", {})
            tool_name = tool.get("_meta", {}).get("codebuddy.ai/toolName")
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
