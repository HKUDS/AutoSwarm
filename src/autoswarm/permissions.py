"""Desk permission modes, named as each agent names its own.

Desk keeps a default mode per agent (its own preference, never the agent's
config) and may override it for one conversation. A Desk conversation and every
call beneath it carry that table: a child starts in its own agent's default but
never above the agent that started it. As everywhere in AutoSwarm, a worker's
remaining native requests go to the agent that started it, which asks the user
when it is unsure. Outside Desk no table exists and every agent keeps its own
settings.
"""
from __future__ import annotations

from .records import SwarmError

# Rank: what an agent may do without asking. 0 read only, 1 asks before edits,
# 2 edits the workspace, 3 an automatic reviewer decides, 4 no checks.
MODES = {
    "claude-code": {"plan": 0, "default": 1, "acceptEdits": 2, "auto": 3, "bypassPermissions": 4},
    "codex": {"read-only": 0, "ask": 2, "approve-for-me": 3, "full-access": 4},
    "cursor": {"plan": 0, "allowlist": 2, "run-everything": 4},
}
DEFAULTS = {"claude-code": "acceptEdits", "codex": "ask", "cursor": "allowlist"}
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
}


def check(provider: str, mode: str) -> str:
    if mode not in MODES.get(provider, {}):
        raise SwarmError("invalid_permission", f"{provider} has no Desk permission mode {mode!r}; choose one of: {', '.join(MODES.get(provider, {})) or 'none'}")
    return mode


def table(value: dict) -> dict:
    if not isinstance(value, dict):
        raise SwarmError("invalid_permission", "Desk permissions must map each agent to one of its modes")
    return {provider: check(provider, mode) for provider, mode in value.items()}


def capped(provider: str, mode: str, limit: int | None) -> str:
    """The mode itself, or this agent's highest mode within the limit."""
    ranks = MODES[provider]
    if limit is None or ranks[mode] <= limit:
        return mode
    allowed = [name for name, rank in ranks.items() if rank <= limit]
    return max(allowed, key=ranks.get) if allowed else min(ranks, key=ranks.get)


def apply(records, task: dict, conversation: dict) -> None:
    """Set the call's mode from its conversation (Desk) or the agent that started it."""
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
    if provider not in MODES:
        return
    # The conversation's own choice (set in Desk), else its agent's Desk default.
    mode = conversation.get("permission") or defaults.get(provider) or DEFAULTS[provider]
    limit = MODES.get(parent["provider"], {}).get(parent.get("permission")) if inherited else None
    task["permission"] = capped(provider, check(provider, mode), limit)
    if FLAGS[provider] & {value.partition("=")[0] for value in task.get("provider_args", [])}:
        raise SwarmError("permission_managed", "Desk sets this agent's permission mode; remove native permission flags from provider args")


def launch(task: dict) -> tuple[list[str], dict]:
    """Native launch flags and protocol options for the call's mode."""
    mode = task.get("permission")
    if not mode:
        return [], {}
    provider = task["provider"]
    if provider == "claude-code":
        return ["--permission-mode", mode], {}
    if provider == "codex":
        return [], dict(CODEX[mode])
    return (["--force"] if mode == "run-everything" else []), {"mode": "plan" if mode == "plan" else "agent"}
