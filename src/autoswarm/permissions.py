"""Desk permission modes, named as each agent names its own.

Every agent starts in Follow: AutoSwarm passes nothing and the agent keeps its own settings.
Desk may instead choose one of the agent's own modes as its default (never writing the agent's
config) and may override it for one conversation. A Desk conversation and every call beneath it
carry that table; a worker starts in its own agent's choice. As everywhere in AutoSwarm, a
worker's remaining native requests go to the agent that started it, which asks the user when it
is unsure. Outside Desk no table exists and every agent keeps its own settings.
"""
from __future__ import annotations

import json

from .records import SwarmError

FOLLOW = "follow"
# Each agent's own modes, from the most to the least asking. Follow comes before all of them.
MODES = {
    "claude-code": ["plan", "default", "acceptEdits", "auto", "bypassPermissions"],
    "codex": ["read-only", "ask", "approve-for-me", "full-access"],
    "cursor": ["plan", "allowlist", "run-everything"],
    "codebuddy": ["plan", "default", "acceptEdits", "auto", "bypassPermissions"],
    "kimi": ["plan", "default", "auto", "yolo"],
    "copilot": ["plan", "agent", "allow-all"],
    "hermes": ["default", "accept_edits", "dont_ask", "yolo"],
    "dsh": ["read-only", "workspace-write", "danger-full-access"],
    "grok": ["always-approve"],
    "gemini": ["plan", "default", "auto_edit", "yolo"],
    "opencode": ["ask", "allow"],
}
# "Auto or above": the agent's own automatic reviewer, or the mode that stops asking when it has none.
AUTO = {"claude-code": "auto", "codex": "approve-for-me", "cursor": "run-everything", "codebuddy": "auto",
        "kimi": "auto", "copilot": "allow-all", "hermes": "yolo", "dsh": "danger-full-access",
        "grok": "always-approve", "gemini": "auto_edit", "opencode": "allow"}
CODEX = {
    "read-only": {"sandbox": "read-only", "approvalPolicy": "on-request", "approvalsReviewer": "user"},
    "ask": {"sandbox": "workspace-write", "approvalPolicy": "on-request", "approvalsReviewer": "user"},
    "approve-for-me": {"sandbox": "workspace-write", "approvalPolicy": "on-request", "approvalsReviewer": "auto_review"},
    "full-access": {"sandbox": "danger-full-access", "approvalPolicy": "never", "approvalsReviewer": "user"},
}
# Native flags that would change what Desk chose.
FLAGS = {
    "claude-code": {"--permission-mode", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"},
    "codex": {"--sandbox", "-s", "--ask-for-approval", "-a", "--full-auto", "--dangerously-bypass-approvals-and-sandbox", "--yolo"},
    "cursor": {"--force", "-f", "--yolo", "--auto-review", "--mode", "--plan"},
    "codebuddy": {"--permission-mode", "-y", "--dangerously-skip-permissions"},
    "copilot": {"--allow-all", "--allow-all-tools", "--allow-all-paths", "--allow-all-urls", "--yolo"},
    "grok": {"--always-approve"},
    "gemini": {"--approval-mode", "-y", "--yolo"},
}


def check(provider: str, mode: str) -> str:
    if mode != FOLLOW and mode not in MODES.get(provider, []):
        raise SwarmError("invalid_permission", f"{provider} has no Desk permission mode {mode!r}; choose one of: {', '.join([FOLLOW, *MODES.get(provider, [])])}")
    return mode


def table(value: dict) -> dict:
    if not isinstance(value, dict):
        raise SwarmError("invalid_permission", "Desk permissions must map each agent to one of its modes")
    return {provider: check(provider, mode) for provider, mode in value.items()}


def apply(records, task: dict, conversation: dict) -> None:
    """Set the call's mode from its conversation (Desk) or the table of the agent that started it."""
    from . import messages
    parent = None
    try:
        owner = messages.agent(records, task["controller_id"]).get("call_id")
        parent = records.require(owner)[1] if owner else None
    except (SwarmError, OSError, ValueError):
        parent = None
    inherited = bool(parent and parent.get("permissions") is not None)
    defaults = parent["permissions"] if inherited else conversation.get("permissions")
    if defaults is None:
        return
    provider = task["provider"]
    task["permissions"] = defaults
    # The conversation's own choice (set in Desk), else its agent's Desk default, else Follow.
    mode = check(provider, conversation.get("permission") or defaults.get(provider) or FOLLOW)
    task["permission"] = mode
    if mode != FOLLOW and FLAGS.get(provider, set()) & {value.partition("=")[0] for value in task.get("provider_args", [])}:
        raise SwarmError("permission_managed", "Desk sets this agent's permission mode; remove native permission flags from provider args")


def launch(task: dict) -> tuple[list[str], dict, dict]:
    """Native launch flags, protocol options and environment for the call's mode."""
    mode = task.get("permission")
    if not mode or mode == FOLLOW:
        return [], {}, {}
    provider = task["provider"]
    if provider in {"claude-code", "codebuddy"}:
        return ["--permission-mode", mode], {}, {}
    if provider == "codex":
        return [], dict(CODEX[mode]), {}
    if provider == "cursor":
        # Cursor's --auto-review is not applied to its ACP server (it stays on its allowlist), so Desk offers no Auto-review.
        return (["--force"] if mode == "run-everything" else []), {"mode": "plan" if mode == "plan" else "agent"}, {}
    if provider == "kimi":
        return [], {"mode": mode}, {}
    if provider == "copilot":
        return (["--allow-all"] if mode == "allow-all" else []), {"mode": "plan" if mode == "plan" else "agent"}, {}
    if provider == "hermes":
        # Hermes' modes cover file edits; YOLO also stops its command approvals.
        return [], {"mode": "dont_ask" if mode == "yolo" else mode}, ({"HERMES_YOLO_MODE": "1"} if mode == "yolo" else {})
    if provider == "dsh":
        return [], {}, {"DSH_PERMISSION_MODE": mode}
    if provider == "grok":
        return ["--always-approve"], {}, {}
    if provider == "gemini":
        # Gemini CLI's --approval-mode values are Desk's modes; --yolo is the same as yolo.
        return (["--yolo"] if mode == "yolo" else ["--approval-mode", mode]), {}, {}
    if provider == "opencode":
        return [], {}, {"OPENCODE_PERMISSION": mode}
    return [], {}, {}


def environment(task: dict, env: dict) -> dict:
    """The process environment with the call's mode, keeping the user's own inline OpenCode config."""
    _, _, values = launch(task)
    if not values:
        return env
    env = dict(env)
    mode = values.pop("OPENCODE_PERMISSION", None)
    if mode:
        try:
            content = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        except ValueError:
            content = {}
        if not isinstance(content, dict):
            content = {}
        # OpenCode 2 takes one action for every permission; OpenCode 1 names its three.
        content["permission"] = mode if task.get("native_transport") == "acp" else {name: mode for name in ("edit", "bash", "webfetch")}
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(content)
    env.update(values)
    return env
