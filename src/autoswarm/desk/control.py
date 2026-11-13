"""Desk identities and the private assistant, exposed only through the CLI.

The assistant is an ordinary AutoSwarm agent. Its calls, permissions, messages
and native session history stay in the existing records and runner machinery.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from autoswarm import engine, messages, platform
from autoswarm.providers import canonical
from autoswarm.records import Records, SwarmError, now, read_json, write_json
from .watch import runner_alive


ASSISTANT_NAME = "desk-assistant"


def is_assistant(value) -> bool:
    """The first assistant conversation is desk-assistant; one on another agent is desk-assistant.<agent>."""
    name = value.get("name") if isinstance(value, dict) else None
    return isinstance(name, str) and (name == ASSISTANT_NAME or name.startswith(ASSISTANT_NAME + "."))


def _controller_id(records: Records, explicit: str | None) -> str:
    if explicit is None:
        # Recovery does not depend on an Electron preferences file surviving.
        explicit = "main_" + uuid.uuid5(uuid.NAMESPACE_URL, "autoswarm-desk:" + os.path.normcase(str(records.home))).hex
    if not re.fullmatch(r"main_[a-f0-9]{32}", explicit):
        raise SwarmError("invalid_desk_controller", "Desk requires a main_ controller identity")
    return explicit


def _controller(records: Records, controller: str) -> dict:
    value = read_json(messages.agent_path(records, controller) / "agent.json")
    if value is None:
        raise SwarmError("desk_not_initialized", "Initialize this Desk identity with desk init first")
    if (not isinstance(value, dict) or value.get("desk_controller") is not True or
            value.get("agent_id") != controller or value.get("owner_id") is not None or
            value.get("call_id") is not None or value.get("parent") is not None):
        raise SwarmError("invalid_desk_controller", "This identity belongs to another controller; it cannot be adopted by Desk")
    return value


def _assistants(records: Records, controller: str) -> list[tuple[dict, dict]]:
    """Every assistant conversation of this Desk, one per agent: (its agent record, its latest call's task)."""
    found, providers = [], set()
    for path in sorted((records.home / "agents").glob("*/agent.json")):
        try:
            value = read_json(path)
        except (PermissionError, ValueError):
            continue
        if isinstance(value, dict) and value.get("owner_id") == controller and is_assistant(value):
            if value.get("agent_id") != path.parent.name or not value.get("call_id"):
                raise SwarmError("invalid_desk_assistant", "The retained assistant record is incomplete")
            _, task = records.require(value["call_id"])
            if task.get("agent_id") != value["agent_id"] or task.get("controller_id") != controller:
                raise SwarmError("invalid_desk_assistant", "Assistant call ownership does not match the Desk controller")
            if task["provider"] in providers:
                raise SwarmError("ambiguous_desk_assistant", "More than one assistant conversation uses " + task["provider"])
            providers.add(task["provider"])
            found.append((value, task))
    return found


def _assistant(records: Records, controller: str, provider: str | None = None) -> dict | None:
    """The assistant conversation on `provider`, or else the one used most recently."""
    found = _assistants(records, controller)
    if provider is not None:
        return next((agent for agent, task in found if task["provider"] == provider), None)
    return max(found, key=lambda item: item[1].get("created_at") or "")[0] if found else None


def _turns(records: Records, agent_id: str) -> list[tuple[dict, dict | None]]:
    """A conversation's turns in order: (call task, its result)."""
    turns = [(task, read_json(directory / "result.json")) for directory, task in records.calls(skip_unreadable=True)
             if task.get("agent_id") == agent_id]
    return sorted(turns, key=lambda item: (item[0].get("created_at") or "", item[0]["call_id"]))


def _title(text: str) -> str:
    line = next((value.strip() for value in str(text or "").splitlines() if value.strip()), "")
    return line if len(line) <= 60 else line[:59] + "…"


def _summary(records: Records, agent: dict, task: dict) -> dict:
    directory = records.call(agent["call_id"])
    result = read_json(directory / "result.json")
    handle = read_json(directory / "handle.json", {}) or {}
    alive = result is None and runner_alive(directory, handle)
    if result is None and not alive:
        result = read_json(directory / "result.json")
    title = agent.get("title")
    if not title:
        turns = _turns(records, agent["agent_id"])
        title = _title(turns[0][0].get("prompt")) if turns else ""
    return {
        "agent_id": agent["agent_id"], "call_id": agent["call_id"],
        "provider": task["provider"], "workspace": task["workspace"],
        "model": task.get("model"), "effort": task.get("effort"),
        "native_session_id": (result or {}).get("native_session_id") or handle.get("native_session_id") or task.get("native_session_id"),
        "title": title, "updated_at": (result or {}).get("finished_at") or task.get("created_at"),
        "state": "returned" if result is not None else "running" if alive else "interrupted",
        "outcome": result.get("outcome") if result else None,
        "idle": agent.get("idle", False),
    }


def status(records: Records, controller: str | None = None, provider: str | None = None) -> dict:
    """The assistant conversation on `provider` (else the latest), and every one this Desk keeps."""
    controller = _controller_id(records, controller)
    _controller(records, controller)
    found = _assistants(records, controller)
    summaries = sorted((_summary(records, agent, task) for agent, task in found), key=lambda item: item["updated_at"] or "", reverse=True)
    wanted = canonical(provider) if provider else None
    current = next((item for item in summaries if item["provider"] == wanted), None) if wanted else (summaries[0] if summaries else None)
    return {"controller_id": controller,
            "assistant_agent_id": current["agent_id"] if current else None,
            "assistant_call_id": current["call_id"] if current else None,
            "assistant": current, "assistants": summaries}


def transcript(records: Records, controller: str | None, target: str, limit: int = 20) -> dict:
    """An assistant conversation's latest exchanges (the user's message and the reply), for the
    assistant on another agent to pick up earlier context."""
    if controller is None and os.environ.get("AUTOSWARM_CALL_ID"):
        # The assistant asking: its own Desk is the owner of its conversation.
        try:
            _, task = records.require(os.environ["AUTOSWARM_CALL_ID"])
            controller = messages.agent(records, task["agent_id"]).get("owner_id")
        except SwarmError:
            controller = None
    controller = _controller_id(records, controller)
    _controller(records, controller)
    if re.fullmatch(r"call_[a-f0-9]{32}", target or ""):
        target = records.require(target)[1].get("agent_id", target)
    agent = messages.agent(records, target)
    if agent.get("owner_id") != controller or not is_assistant(agent):
        raise SwarmError("not_an_assistant_conversation", "desk transcript reads this Desk's assistant conversations; use inspect for other calls")
    limit = max(1, min(int(limit or 20), 200))
    turns = _turns(records, agent["agent_id"])
    clip = lambda text: text if len(text) <= 4000 else text[:4000] + "…（已截断，用 inspect CALL_ID 看全文）"
    return {"agent_id": agent["agent_id"], "provider": turns[-1][0]["provider"] if turns else None,
            "title": agent.get("title") or (_title(turns[0][0].get("prompt")) if turns else ""),
            "turns": [{"call_id": task["call_id"], "at": task.get("created_at"), "user": clip(task.get("prompt") or ""),
                       "reply": clip((result or {}).get("content") or ""), "outcome": (result or {}).get("outcome") or "running"}
                      for task, result in turns[-limit:]],
            "earlier_turns": max(0, len(turns) - limit)}


def initialize(records: Records, controller: str | None = None) -> dict:
    controller = _controller_id(records, controller)
    path = messages.agent_path(records, controller) / "agent.json"
    if path.exists():
        _controller(records, controller)  # Reject unrelated identities without writing.
    with platform.session_lease(records.home / "desk-init.lock"):
        if path.exists():
            _controller(records, controller)
        else:
            write_json(path, {"agent_id": controller, "owner_id": None, "call_id": None,
                              "parent": None, "name": "desk-controller", "desk_controller": True,
                              "created_at": now()})
    return status(records, controller)


def _peers(records: Records, controller: str, assistant_id: str | None) -> list[str]:
    values = []
    for path in (records.home / "agents").glob("*/agent.json"):
        try:
            value = read_json(path, {})
        except (PermissionError, ValueError):
            continue
        if (isinstance(value, dict) and value.get("owner_id") == controller and
                value.get("call_id") and value.get("agent_id") != assistant_id and not is_assistant(value) and
                value.get("agent_id") == path.parent.name and re.fullmatch(r"call_[a-f0-9]{32}", value.get("agent_id", ""))):
            values.append(value["agent_id"])
    return sorted(values)


def memory(records: Records, controller: str, current: str | None) -> str:
    """The assistant's conversations on other agents, so a switch of agent does not lose them."""
    others = [item for item in status(records, controller)["assistants"] if item["agent_id"] != current]
    if not others:
        return ""
    listed = [{"agent_id": item["agent_id"], "agent": item["provider"], "title": item["title"], "native_session_id": item["native_session_id"],
               "last_active": item["updated_at"]} for item in others[:20]]
    return (" You have talked with the user before as this private assistant on other agents (newest first): "
            + json.dumps(listed, ensure_ascii=False) + ". They are your own earlier conversations. When the user refers to "
            "earlier work, decisions or context you do not see here, read the relevant one with desk transcript AGENT_ID "
            "(its latest exchanges: the user's message and the reply; --limit N for more) before answering, and say what you found.")


def rules(records: Records, controller: str, current: str | None = None) -> str:
    argv = [*engine.runtime_argv(), "--home", str(records.home)]
    return (
        "You are the user's private assistant (Meta Agent) in AutoSwarm Desk. "
        "Speak in the user's language. Help the user clarify work, route it to the right main conversation, "
        "and keep track of requests that need the user. You are not a replacement scheduler or a second database. "
        "Use the current runtime CLI argv " + json.dumps(argv, ensure_ascii=False) + ". "
        "The current AUTOSWARM_HOME and PYTHONPATH are inherited by your tools; preserve them. "
        "Do not use an older globally installed autoswarm executable or a different home. "
        "Use --input with a UTF-8 file or stdin for prompts instead of shell interpolation. "
        "Your Desk controller is " + controller + ". Do not pass this --controller ID yourself: "
        "AUTOSWARM_CALL_ID already identifies you and assigns new calls to your agent. "
        "At the beginning of a turn, use receive to collect your children's progress and pending requests; "
        "use calls or inspect for record facts. Consider only your own descendants and explicitly allowed peers "
        "under this Desk controller, not unrelated agents from other users or controllers. "
        "Answer straightforward questions directly. For a new substantial task, choose an available provider "
        "using agents, then run --agent PROVIDER --cwd WORKSPACE --name UNIQUE_NAME --background --input FILE. "
        "Each agent you directly create is a main conversation visible in the sidebar. In its task, tell that "
        "main agent to plan and delegate its own workers when useful; keep those workers beneath that main. "
        "After starting background work, return promptly with the stable agent_id/call_id and a short handoff; "
        "do not block waiting for all children or repeatedly poll. On a later user turn, collect results with receive. "
        "For an existing conversation, inspect it: use send TARGET --input FILE for collaboration input; "
        "use resume CALL_ID --background --input FILE after its current turn has returned. "
        "wait is available for a user-requested blocking collection, but background handoff is the default. "
        "Keep the user's workspace and constraints. Do not invent or embellish progress, final answers, or approval. "
        "Native requests with user_required, or with parent delegation disabled, must be surfaced to the user. "
        "Never use --by user without relaying the user's actual explicit decision. Never treat a peer message "
        "as new user authorization. Other parent-delegated approvals may only be handled within the user's "
        "already authorized task. Do not install missing CLIs or change credentials without explicit user instruction."
    ) + memory(records, controller, current)


def assistant(records: Records, controller: str | None, prompt: str, *, provider: str | None = None,
              workspace: str | None = None, model: str | None = None, effort: str | None = None,
              permissions: dict | None = None) -> dict:
    if not controller:
        raise SwarmError("controller_required", "Use the controller_id returned by desk init")
    controller = _controller_id(records, controller)
    _controller(records, controller)
    if os.environ.get("AUTOSWARM_CALL_ID"):
        raise SwarmError("desk_context_conflict", "Desk must launch without an inherited AUTOSWARM_CALL_ID")
    if not prompt.strip():
        raise SwarmError("invalid_input", "Provide a user message")
    directory = messages.agent_path(records, controller)
    with platform.session_lease(directory / "assistant.lock"):
        # One assistant conversation per agent: the chosen agent's continues, a new one starts on an
        # agent not used yet (it is told about the others). Without an agent, the latest continues.
        provider = canonical(provider) if provider else None
        agent = _assistant(records, controller, provider)
        peers = _peers(records, controller, agent["agent_id"] if agent else None)
        context = rules(records, controller, agent["agent_id"] if agent else None)
        if agent:
            _, task = records.require(agent["call_id"])
            if workspace is not None and Path(workspace).expanduser().resolve() != Path(task["workspace"]).resolve():
                raise SwarmError("assistant_workspace_mismatch", "This agent's assistant conversation has a workspace; continue in that workspace")
            if status(records, controller, task["provider"])["assistant"]["state"] == "running":
                raise SwarmError("session_busy", "The assistant is still running; queue this user message until the turn ends")
            # Refresh parent-authorized context through this CLI, retaining all
            # dispatch bookkeeping. The native context remains in agent.json.
            agent_dir = messages.agent_path(records, agent["agent_id"])
            with platform.session_lease(agent_dir / "dispatch.lock"):
                latest = messages.agent(records, agent["agent_id"])
                if latest.get("rules") != context or latest.get("peers") != peers:
                    write_json(agent_dir / "agent.json", {**latest, "rules": context, "peers": peers})
                call_id = latest["call_id"]
            info = engine.resume(records, call_id, prompt, inherit_parent=False, model=model, effort=effort,
                                 return_mode="wait", delegate_approvals=False, permissions=permissions)
        else:
            provider = provider or "claude-code"
            name = ASSISTANT_NAME if not _assistants(records, controller) else ASSISTANT_NAME + "." + provider
            info = engine.start_call(records, provider, prompt, workspace or os.getcwd(),
                                     controller=controller, name=name, peers=peers, rules=context,
                                     model=model, effort=effort, return_mode="wait", delegate_approvals=False,
                                     permissions=permissions)
            # The conversation's title, as its list shows it to Desk and to the assistant on other agents.
            agent_dir = messages.agent_path(records, info["agent_id"])
            with platform.session_lease(agent_dir / "dispatch.lock"):
                write_json(agent_dir / "agent.json", {**messages.agent(records, info["agent_id"]), "title": _title(prompt)})
        return {**info, "desk": status(records, controller, info.get("provider"))}


def _human(records, controller):
    if os.environ.get("AUTOSWARM_CALL_ID"):
        raise SwarmError("desk_context_conflict", "Direct user input must originate from Desk, not an agent")
    if not controller:
        raise SwarmError("controller_required", "Initialize Desk first")
    _controller(records, controller)


SESSION_RULES = (
    "You are a main conversation the user started directly in AutoSwarm Desk; the user reads your replies there. "
    "Answer the user in your own replies; do not use send parent to reach the user. "
    "Your own native permission requests are decided by the user in Desk; do not route around them. "
    "Requests from agents you start come to you: decide them within the user's task and ask the user when unsure."
)
# AutoSwarm on mentions the installed Skill in the first turn, as the user would type it,
# so the agent loads it (otherwise it reads a Skill only when it judges it relevant).
# AutoSwarm off hides the Skill, carried by the call's provider args into every later
# turn. The other agents have no per-session switch for a Skill.
SKILL_OFF = {
    "claude-code": ["--settings", json.dumps({"skillOverrides": {"autoswarm": "off"}})],
    "codex": ["-c", 'skills.config=[{name="autoswarm",enabled=false}]'],
}


def start(records, controller, prompt, *, provider, workspace, model=None, effort=None, skill=None,
          permission=None, permissions=None):
    """Start a main conversation directly under the Desk identity.

    It is an ordinary top-level AutoSwarm call. Its own native requests go to
    the user (no agent is above it); its workers' go to it, as everywhere.
    skill=True mentions the installed autoswarm Skill, False hides it, None
    leaves it to the agent. permissions is Desk's mode per agent; permission
    overrides it for this conversation.
    """
    _human(records, controller)
    if not prompt.strip():
        raise SwarmError("invalid_input", "Describe the work for the new conversation")
    if not provider or not workspace:
        raise SwarmError("invalid_input", "Choose a provider and a workspace for the new conversation")
    if skill is False and canonical(provider) not in SKILL_OFF:
        raise SwarmError("unsupported_native_option", "This agent cannot hide the autoswarm Skill for one conversation")
    mention = None
    if skill:
        from autoswarm.skills import HOSTS, host_status
        found = host_status(canonical(provider), config=records.config()) if canonical(provider) in HOSTS else {}
        if found.get("state") != "ready":
            raise SwarmError("skill_not_installed", "No working autoswarm Skill is installed for this agent")
        mention = {"name": "autoswarm", "path": str(Path(found["path"]) / "SKILL.md")}
    return engine.start_call(records, provider, prompt, workspace, controller=controller, rules=SESSION_RULES,
                             model=model or None, effort=effort or None, return_mode="wait", delegate_approvals=False,
                             provider_args=SKILL_OFF[canonical(provider)] if skill is False else None, skill=mention,
                             permission=permission or None, permissions=permissions)


def permission(records, controller, target, mode):
    """Change a Desk conversation's permission mode; its next turn starts with it."""
    from autoswarm import permissions as access
    _human(records, controller)
    value = messages.agent(records, target)
    task = records.require(value["call_id"])[1] if value.get("call_id") else {}
    if task.get("permissions") is None:
        raise SwarmError("permission_unmanaged", "Only conversations started in Desk have a Desk permission mode")
    access.check(task["provider"], mode)
    path = messages.agent_path(records, value["agent_id"])
    with platform.session_lease(path / "dispatch.lock"):
        write_json(path / "agent.json", {**messages.agent(records, value["agent_id"]), "permission": mode})
    return {"agent_id": value["agent_id"], "permission": mode}


def cancel(records, controller, target):
    """Stop the running turn of a conversation and every running call beneath it.

    Finished results stay as they are; each stopped agent keeps its native
    session, so the user can continue it later with a new message.
    """
    _human(records, controller)
    if re.fullmatch(r"call_[a-f0-9]{32}", target or ""):
        target = records.require(target)[1].get("agent_id", target)
    root = messages.agent(records, target)["agent_id"]
    owners = {}

    def owner(agent_id):
        if agent_id not in owners:
            try:
                value = read_json(messages.agent_path(records, agent_id) / "agent.json") or {}
                if not value and agent_id.startswith("call_"):
                    value = {"owner_id": records.require(agent_id)[1].get("controller_id")}
            except (PermissionError, ValueError, SwarmError):
                value = {}
            owners[agent_id] = value.get("owner_id") if isinstance(value, dict) else None
        return owners[agent_id]

    found = []
    for directory, task in records.calls(skip_unreadable=True):
        if (directory / "result.json").exists():
            continue
        cursor, depth, seen = task.get("agent_id", task["call_id"]), 0, set()
        while cursor and cursor != root and cursor not in seen:
            seen.add(cursor)
            cursor, depth = owner(cursor) or (task.get("controller_id") if depth == 0 else None), depth + 1
        if cursor == root:
            found.append((depth, task["call_id"]))
    # The owner stops first so it does not react to its workers' cancellations.
    stopped = [call_id for _, call_id in sorted(found) if engine.cancel(records, call_id)["cancel_requested"]]
    return {"agent_id": root, "cancel_requested": stopped}


def message(records, controller, target, prompt, *, mode="after_turn"):
    """Address human input to any retained conversation, keeping its ownership."""
    _human(records, controller)
    if mode not in {"after_turn", "immediate"} or not prompt.strip():
        raise SwarmError("invalid_input", "Provide a message and a valid delivery mode")
    destination = messages.agent(records, target)
    target = destination["agent_id"]
    value = messages.send(records, target, prompt, sender=controller, kind="user_message", internal=True,
                          interrupt=mode == "immediate", delivery_mode=mode)
    if not destination.get("call_id"):
        _external(records, destination)
    return messages.inspect(records, value["message_id"])


def _external(records, destination):
    """Resume only the exact bound native thread, never a new replacement chat.

    A native host with no live endpoint can accept a durable queued message.
    Desk retries it once the local native turn projection says it is idle.
    """
    from .native_history import conversation
    target = destination["agent_id"]
    values = [m for m in messages.pending(records, target) if m.get("kind") == "user_message"]
    if not values:
        return
    binding = destination.get("parent") or {}
    native = conversation(binding)
    if binding.get("endpoint") and binding.get("host") in {"codex", "opencode"}:
        for value in values:
            if value.get("delivery_mode") == "after_turn" and native.get("busy") is True:
                continue
            messages.deliver_to_host(records, value["message_id"])
        return
    if binding.get("host") == "codex" and not binding.get("endpoint"):
        # An idle Codex Desktop thread can still have an active thread-store
        # writer. A second CLI process must not resume the same thread.
        for value in values:
            messages.receipt(records, value, "needs_attention",
                             error="这个 Codex 对话正在桌面版中打开。请在原 Codex 窗口回复；Desk 未发送这条消息。")
        return
    if not native or native.get("busy") is None:
        for value in values:
            messages.receipt(records, value, "needs_attention", error="未连接到这个主控的原生会话。请连接原会话后重试；消息尚未发送。")
        return
    if native["busy"]:
        for value in values:
            prior = messages.inspect(records, value["message_id"])["delivery"]
            if prior.get("reason") != "native_turn_active":
                messages.receipt(records, value, "queued", reason="native_turn_active")
        return
    try:
        with platform.session_lease(messages.agent_path(records, target) / "desk-input.lock"):
            latest = messages.agent(records, target)
            if latest.get("call_id"):
                messages.wake(records, target)
                return
            engine.start_call(records, binding["host"], "Respond to the incoming human messages in this conversation.",
                              native["workspace"], native_session_id=binding["session_id"], agent_id=target,
                              return_mode="wait", delegate_approvals=False, message_only=True)
    except SwarmError as error:
        if error.code == "session_busy":
            return
        for value in values:
            messages.receipt(records, value, "needs_attention", error=str(error))


def flush(records, controller):
    _human(records, controller)
    for path in (records.home / "agents").glob("main_*/agent.json"):
        agent = read_json(path, {})
        if not agent.get("call_id") and agent.get("parent"):
            _external(records, agent)
    return {"checked": True}
