"""Muse Session Protocol, using the native host and its durable sessions."""
import json
import time
import uuid

from . import platform
from .records import SwarmError


def command_id():
    # MSP requires a UUIDv7 idempotency key; Python 3.10 has no uuid.uuid7.
    bits = ((time.time_ns() // 1_000_000) << 80) | (uuid.uuid4().int & ((1 << 80) - 1))
    bits = (bits & ~(0xf << 76)) | (7 << 76)
    bits = (bits & ~(3 << 62)) | (2 << 62)
    return str(uuid.UUID(int=bits))


class Muse:
    def __init__(self, native):
        self.n = native
        self.accepting = False
        self.approvals = {}

    def start(self):
        self.n.stage("handshake")
        self.n.request("initialize", {"clientInfo": {"name": "autoswarm", "version": "2.0.0.dev0"}, "capabilities": {"userInputDialogs": False}}, self.initialized)

    def initialized(self, result):
        if result.get("schema", {}).get("version") != 1:
            raise SwarmError("native_protocol_error", "Unsupported Muse MSP schema version")
        self.n.send({"method": "initialized"})
        params = {"commandId": command_id()}
        session = self.n.task.get("native_session_id")
        if session:
            params.update({"sessionId": session, "excludeItems": True})
        else:
            params["workspaceRoot"] = self.n.task["workspace"]
        self.n.stage("session")
        self.n.request("session/resume" if session else "session/start", params, self.started)

    def started(self, result):
        self.n.session(result["session"]["sessionId"])
        self.accepting = True
        self.n.stage("execution")
        self.n.request("turn/start", {"commandId": command_id(), "sessionId": self.n.output.session_id, "input": [{"type": "text", "text": self.n.task["prompt"]}]}, lambda _: None)

    def approval(self, params):
        native_id = params["approvalId"]
        requirement = params["currentRequirementId"]
        key = native_id + ":" + json.dumps(requirement, sort_keys=True)
        previous = self.approvals.get(native_id)
        if previous and previous != key:
            self.n.broker.withdraw(previous, "native_requirement_updated")
        self.approvals[native_id] = key
        choices = params.get("availableChoices", [])
        scopes = ["once"] + (["session"] if any(item.get("decision") == "approvedForSession" and item.get("scope") == "session" for item in choices) else [])

        def reply(value):
            decision = "approvedForSession" if value["scope"] == "session" else "approved"
            if value["decision"] == "deny":
                # Native safety reviews can offer abort rather than denied.
                decision = "denied" if any(item.get("decision") == "denied" for item in choices) else "abort"
            choice = next((item for item in choices if item.get("decision") == decision and (value["decision"] == "deny" or item.get("scope") == value["scope"])), None)
            if not choice:
                raise SwarmError("unsupported_approval_scope", "Muse did not offer this approval decision")
            body = {"sessionId": self.n.output.session_id, "commandId": command_id(), "approvalId": native_id, "requirementId": requirement, "choiceId": choice["choiceId"]}
            if value.get("message") and choice.get("acceptsFeedback"):
                body["feedback"] = value["message"]
            self.n.request("approval/decide", body, lambda _: None)

        self.n.broker.request(key, self.n.output.session_id, params, reply, scopes=scopes)

    def feed(self, event):
        method, params = event.get("method"), event.get("params", {})
        if params.get("sessionId") != self.n.output.session_id or not self.accepting:
            if "id" in event:
                self.n.send({"id": event["id"], "error": {"code": -32601, "message": "Unsupported native MSP interaction or session: " + str(method)}})
            return
        if method == "item/completed" and params.get("item", {}).get("kind") == "agentMessage":
            self.n.output.content = params["item"].get("text", "")
        elif method == "turn/completed":
            self.n.output.terminal = params.get("terminal") == "completed"
            self.n.output.cancelled = params.get("terminal") == "cancelled"
            self.n.output.error = str(params["error"]) if params.get("error") else None
            self.n.output.finish_reason = params.get("terminal")
            self.n.end_input()
            platform.stop_child(self.n.process)
        elif method in {"approval/request", "approval/requested", "approval/updated"}:
            if "id" in event:
                self.n.send({"id": event["id"], "result": {}})
            self.approval(params)
        elif method == "approval/resolved":
            previous = self.approvals.get(params.get("approvalId"))
            if previous:
                self.n.broker.withdraw(previous, "resolved_by_native")
        elif "id" in event:
            self.n.send({"id": event["id"], "error": {"code": -32601, "message": "Unsupported native MSP interaction: " + str(method)}})
