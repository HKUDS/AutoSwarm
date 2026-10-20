"""Runtime dispatcher for the ``opc-collab`` CLI."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from opc.core.company_tools import (
    COLLAB_PROFILE_DISABLED,
    resolve_allowed_collaboration_tools,
    resolve_task_collaboration_tools,
)
from opc.core.events import EventBus
from opc.core.models import AgentMessage, MessageUrgency
from opc.database.store import OPCStore
from opc.layer2_organization.collaboration_service import (
    CollaborationContext,
    CollaborationService,
)
from opc.layer2_organization.communication import CommunicationManager
from opc.layer2_organization.work_item_links import set_linked_work_item_id
from opc.layer4_tools.collaboration import create_collaboration_tools


Handler = Callable[[dict[str, Any], Optional[Mapping[str, str]]], Awaitable[dict[str, Any]]]
BoundHandler = Callable[[dict[str, Any], "CollaborationRuntimeBinding"], Awaitable[dict[str, Any]]]


@dataclass
class CollaborationRuntimeBinding:
    service: CollaborationService
    context: CollaborationContext
    store: OPCStore | None
    manager: CommunicationManager
    env: Mapping[str, str] | None = None
    allowed_tools: set[str] | None = None
    owns_store: bool = False


def _env(name: str, default: str = "", env: Mapping[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    return str(source.get(name, default))


def _parse_allowed_tools(raw: str) -> set[str]:
    payload = str(raw or "").strip()
    if not payload:
        return set()
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in payload.split(",") if item.strip()]
    if isinstance(parsed, list):
        return {str(item).strip() for item in parsed if str(item).strip()}
    return set()


async def build_collaboration_runtime() -> tuple[
    CollaborationService,
    CollaborationContext,
    OPCStore | None,
    CommunicationManager,
]:
    return await build_collaboration_runtime_from_env(None)


async def build_collaboration_runtime_from_env(
    env: Mapping[str, str] | None = None,
) -> tuple[
    CollaborationService,
    CollaborationContext,
    OPCStore | None,
    CommunicationManager,
]:
    """Build the collaboration service/context from the current process env."""
    store: OPCStore | None = None
    task = None
    db_path = _env("OPC_PROJECT_DB_PATH", env=env)
    task_id = _env("OPC_TASK_ID", env=env) or _env("OPC_RUNTIME_TASK_ID", env=env)
    if db_path:
        store = OPCStore(db_path)
        await store.initialize(run_startup_maintenance=False)
        manager = CommunicationManager(store, EventBus())
        if task_id:
            task = await store.get_task(task_id)
    else:
        manager = CommunicationManager(None, EventBus())

    from_role = _env("OPC_COMMS_FROM", env=env)
    context = (
        CollaborationContext.from_task(task, role_id=from_role)
        if task is not None
        else CollaborationContext.from_environment(
            role_id=from_role,
            project_id=_env("OPC_COMMS_PROJECT", "default", env=env),
            session_id=_env("OPC_COMMS_SESSION", "default", env=env),
            workspace_root=_env("OPC_WORKSPACE_ROOT", env=env) or _env("OPC_COMMS_ROOT", env=env) or os.getcwd(),
            task_id=task_id,
        )
    )
    work_item_id = _env("OPC_WORK_ITEM_ID", env=env)
    if work_item_id and task is not None:
        set_linked_work_item_id(task, work_item_id)
    if work_item_id and "linked_work_item_id" not in context.metadata:
        context.metadata["linked_work_item_id"] = work_item_id
    service = CollaborationService(manager)
    return service, context, store, manager


async def build_collaboration_runtime_binding_from_env(
    env: Mapping[str, str] | None = None,
) -> CollaborationRuntimeBinding:
    service, context, store, manager = await build_collaboration_runtime_from_env(env)
    return CollaborationRuntimeBinding(
        service=service,
        context=context,
        store=store,
        manager=manager,
        env=env,
        allowed_tools=allowed_tool_names(task=context.task, context=context, manager=manager, env=env),
        owns_store=store is not None,
    )


def allowed_tool_names(
    *,
    task: Any,
    context: CollaborationContext,
    manager: CommunicationManager,
    env: Mapping[str, str] | None = None,
) -> set[str]:
    explicit = _parse_allowed_tools(_env("OPC_ALLOWED_COLLAB_TOOLS", env=env))
    if explicit:
        return explicit
    role_cfg = None
    org_engine = getattr(manager, "org_engine", None)
    if org_engine is not None and context.role_id:
        try:
            role_cfg = org_engine.get_agent(context.role_id)
        except Exception:
            role_cfg = None
    profile = str(_env("OPC_COLLAB_PROFILE", env=env) or "").strip() or resolve_task_collaboration_tools(
        task,
        role=context.role_id,
        seat=str(getattr(task, "metadata", {}).get("delegation_seat_id", "") or "").strip()
        if task is not None
        else "",
        runtime_state={
            "manager_board_summary": (
                dict(getattr(task, "context_snapshot", {}).get("manager_board_summary", {}) or {})
                if task is not None
                else {}
            ),
        },
        role_cfg=role_cfg,
        debug_admin=str(_env("OPC_MAILBOX_MODE", env=env) or "").strip().lower() == "debug_admin",
    )[0]
    if profile == COLLAB_PROFILE_DISABLED:
        return set()
    return resolve_allowed_collaboration_tools(profile, task=task, runtime_state={})


def _simple_message_payload(message: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(message.get("metadata", {}) or {})
    return {
        "msg_id": str(message.get("msg_id", "") or message.get("message_id", "")).strip(),
        "from_agent": str(message.get("from_agent", "") or message.get("from", "")).strip(),
        "subject": str(message.get("subject", "")).strip(),
        "body": str(message.get("body", "")).strip(),
        "reply_needed": bool(message.get("reply_needed", False)),
        "urgency": str(message.get("urgency", "") or "normal").strip() or "normal",
        "transport_kind": str(message.get("transport_kind", "") or metadata.get("transport_kind", "")).strip(),
        "semantic_type": str(message.get("semantic_type", "") or metadata.get("semantic_type", "")).strip(),
        "status": str(message.get("status", "") or "").strip(),
        "metadata": metadata,
    }


async def _run_bound_handler(
    handler: BoundHandler,
    args: dict[str, Any],
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    binding = await build_collaboration_runtime_binding_from_env(env)
    try:
        return await handler(args, binding)
    finally:
        if binding.owns_store and binding.store is not None:
            await binding.store.close()


def _env_handler(handler: BoundHandler) -> Handler:
    async def _handler(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
        return await _run_bound_handler(handler, args, env)

    return _handler


async def _handle_inbox_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    service = binding.service
    context = binding.context
    if context.task is None:
        return {"error": "`inbox` requires an active task context."}
    raw_ids = args.get("message_ids", [])
    if isinstance(raw_ids, str):
        message_ids = [raw_ids]
    else:
        message_ids = [str(item).strip() for item in list(raw_ids or []) if str(item).strip()]
    return await service.inbox(
        context,
        agent_id=context.role_id,
        task=context.task,
        action=str(args.get("action", "status") or "status").strip(),
        message_ids=message_ids,
        limit=int(args.get("limit", 10) or 10),
    )


async def _handle_inbox(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_inbox_bound, args, env)


async def _handle_send_dm_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    if "blocking" in args:
        return {"error": "`send_dm` no longer accepts `blocking`; use `ask_peer_and_wait`."}
    context = binding.context
    manager = binding.manager
    tool = next(tool for tool in create_collaboration_tools(manager) if tool.name == "send_dm")
    result = await tool.func(task=context.task, **dict(args or {}))
    return result if isinstance(result, dict) else {"result": result}


async def _handle_send_dm(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_send_dm_bound, args, env)


async def _handle_ask_peer_and_wait_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    service = binding.service
    context = binding.context
    if context.task is None:
        return {"error": "`ask_peer_and_wait` requires an active task context."}
    return await service.ask_peer_and_wait(
        context,
        task=context.task,
        to_agent=str(args.get("to_agent", "")).strip(),
        subject=str(args.get("subject", "")).strip(),
        body=str(args.get("body", "")).strip(),
        timeout_action=str(args.get("timeout_action", "")).strip(),
        timeout_seconds=int(args.get("timeout_seconds", 300) or 300),
        on_timeout=str(args.get("on_timeout", "continue") or "continue").strip(),
    )


async def _handle_ask_peer_and_wait(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_ask_peer_and_wait_bound, args, env)


async def _handle_read_inbox_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    if "mark_read" in args:
        return {"error": "`read_inbox` no longer accepts `mark_read`; reads always archive to seen."}
    service = binding.service
    context = binding.context
    messages = await service.read_inbox(
        context,
        agent_id=context.role_id,
        task=context.task,
        task_id=context.task_id or None,
        unread_only=True,
        limit=int(args.get("limit", 10) or 10),
        mark_read=True,
    )
    return {
        "count": len(messages),
        "messages": [_simple_message_payload(message) for message in messages],
    }


async def _handle_read_inbox(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_read_inbox_bound, args, env)


async def _handle_reply_message_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    service = binding.service
    context = binding.context
    reply = await service.reply_message(
        context,
        original_msg_id=str(args.get("message_id", "")).strip(),
        from_agent=context.role_id,
        body=str(args.get("body", "")).strip(),
        subject=str(args.get("subject", "")).strip(),
        task_id=context.task_id or None,
    )
    return {"delivered": True, "message": _simple_message_payload(service.host._serialize_message(reply))}


async def _handle_reply_message(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_reply_message_bound, args, env)


async def _handle_broadcast_issue_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    service = binding.service
    context = binding.context
    task = context.task
    message = AgentMessage(
        msg_type="flag_issue",
        from_agent=context.role_id,
        to_agents=[str(item).strip() for item in list(args.get("to_agents", []) or []) if str(item).strip()],
        subject=str(args.get("subject", "")).strip(),
        body=str(args.get("body", "")).strip(),
        context_ref=getattr(task, "id", None),
        task_id=getattr(task, "id", None),
        urgency=MessageUrgency.HIGH,
        metadata={
            "broadcast": True,
            "async_mailbox": True,
            "reply_requested": False,
        },
    )
    delivered = await service.send_dm(context, message, task=task)
    return {"delivered": True, "message": _simple_message_payload(service.host._serialize_message(delivered))}


async def _handle_broadcast_issue(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_broadcast_issue_bound, args, env)


async def _handle_list_colleagues_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    _ = args
    return await binding.service.list_colleagues(binding.context)


async def _handle_list_colleagues(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_list_colleagues_bound, args, env)


async def _handle_start_meeting_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    service = binding.service
    context = binding.context
    if context.task is None:
        return {"error": "`start_meeting` requires an active task context."}
    return await service.open_meeting_wait(
        context,
        task=context.task,
        topic=str(args.get("topic", "")).strip(),
        participants=[str(item).strip() for item in list(args.get("participants", []) or []) if str(item).strip()],
        agenda=[str(item).strip() for item in list(args.get("agenda", []) or []) if str(item).strip()],
        shared_context=str(args.get("shared_context", "")).strip(),
        decision_owner=str(args.get("decision_owner", "")).strip() or None,
        decision_policy=str(args.get("decision_policy", "semantic_consensus_then_owner") or "semantic_consensus_then_owner").strip(),
        timeout_seconds=int(args.get("timeout_seconds", 900) or 900),
        risk_level=str(args.get("risk_level", "normal") or "normal").strip(),
    )


async def _handle_start_meeting(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_start_meeting_bound, args, env)


async def _handle_respond_meeting_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    service = binding.service
    context = binding.context
    meeting = await service.respond_to_meeting(
        context,
        room_id=str(args.get("meeting_id", "")).strip(),
        from_agent=context.role_id,
        content=str(args.get("content", "")).strip(),
        finalize=bool(args.get("finalize", False)),
        task=context.task,
    )
    return {
        "meeting": {
            "room_id": meeting.room_id,
            "status": meeting.status.value,
            "outcome": meeting.outcome,
        }
    }


async def _handle_respond_meeting(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_respond_meeting_bound, args, env)


async def _handle_read_meeting_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    return await binding.service.read_meeting(binding.context, meeting_id=str(args.get("meeting_id", "")).strip())


async def _handle_read_meeting(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_read_meeting_bound, args, env)


async def _handle_propose_task_adjustment_bound(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    return await binding.service.propose_task_adjustment(
        binding.context,
        summary=str(args.get("summary", "")).strip(),
        changeset=dict(args.get("changeset", {}) or {}),
    )


async def _handle_propose_task_adjustment(args: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return await _run_bound_handler(_handle_propose_task_adjustment_bound, args, env)


def _native_handler_bound(tool_name: str) -> BoundHandler:
    async def _handler(args: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
        native_defs = {tool.name: tool for tool in create_collaboration_tools(binding.manager)}
        tool = native_defs.get(tool_name)
        if tool is None:
            return {"error": f"unknown tool: {tool_name}"}
        tool_args = dict(args or {})
        if tool_name == "respond_meeting" and "meeting_id" in tool_args and "room_id" not in tool_args:
            tool_args["room_id"] = tool_args.pop("meeting_id")
        result = await tool.func(task=binding.context.task, **tool_args)
        return result if isinstance(result, dict) else {"result": result}

    return _handler


def _native_handler(tool_name: str) -> Handler:
    return _env_handler(_native_handler_bound(tool_name))


BOUND_HANDLERS: dict[str, BoundHandler] = {
    "inbox": _handle_inbox_bound,
    "send_dm": _handle_send_dm_bound,
    "ask_peer_and_wait": _handle_ask_peer_and_wait_bound,
    "request_user_input": _native_handler_bound("request_user_input"),
    "read_inbox": _handle_read_inbox_bound,
    "reply_message": _handle_reply_message_bound,
    "broadcast_issue": _handle_broadcast_issue_bound,
    "list_colleagues": _handle_list_colleagues_bound,
    "start_meeting": _handle_start_meeting_bound,
    "respond_meeting": _handle_respond_meeting_bound,
    "read_meeting": _handle_read_meeting_bound,
    "propose_task_adjustment": _handle_propose_task_adjustment_bound,
    "route_work": _native_handler_bound("route_work"),
    "close_human_review": _native_handler_bound("close_human_review"),
    "delegate_work": _native_handler_bound("delegate_work"),
    "modify_work_item": _native_handler_bound("modify_work_item"),
    "delete_work_item": _native_handler_bound("delete_work_item"),
    "manager_board_read": _native_handler_bound("manager_board_read"),
}

HANDLERS: dict[str, Handler] = {
    name: _env_handler(handler)
    for name, handler in BOUND_HANDLERS.items()
}


def _is_infrastructure_error_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    markers = (
        "disk i/o error",
        "database is locked",
        "readonly database",
        "unable to open database file",
        "collaboration broker rpc",
        "broker rpc",
        "sqlite",
    )
    return any(marker in text for marker in markers)


def infrastructure_error_payload(error: Any, *, tool_name: str = "") -> dict[str, Any]:
    payload = {
        "error": str(error or "collaboration infrastructure error"),
        "error_type": "infrastructure",
        "retryable": True,
    }
    if tool_name:
        payload["tool_name"] = tool_name
    return payload


async def _mailbox_notice_bound(binding: CollaborationRuntimeBinding) -> dict[str, Any] | None:
    service = binding.service
    context = binding.context
    if context.task is None or not context.role_id:
        return None
    status = await service.inbox(
        context,
        agent_id=context.role_id,
        task=context.task,
        action="status",
        limit=3,
    )
    if not bool(status.get("has_actionable_unread", False)):
        return None
    return {
        "has_actionable_unread": True,
        "unread_count": int(status.get("unread_count", 0) or 0),
        "actionable_count": int(status.get("actionable_count", 0) or 0),
        "blocking_count": int(status.get("blocking_count", 0) or 0),
        "latest_unread_summary": list(status.get("latest_unread_summary", []) or [])[:3],
        "hint": "Call `opc-collab inbox --args-stdin` with JSON {\"action\":\"peek\"} to inspect, then reply or ack handled messages.",
    }


async def _mailbox_notice(env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    binding = await build_collaboration_runtime_binding_from_env(env)
    try:
        return await _mailbox_notice_bound(binding)
    finally:
        if binding.owns_store and binding.store is not None:
            await binding.store.close()


async def _attach_mailbox_notice_bound(payload: dict[str, Any], binding: CollaborationRuntimeBinding) -> dict[str, Any]:
    try:
        notice = await _mailbox_notice_bound(binding)
    except Exception:
        notice = None
    if notice:
        payload.setdefault("mailbox_notice", notice)
    return payload


async def _attach_mailbox_notice(payload: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    binding = await build_collaboration_runtime_binding_from_env(env)
    try:
        return await _attach_mailbox_notice_bound(payload, binding)
    finally:
        if binding.owns_store and binding.store is not None:
            await binding.store.close()


async def dispatch_collaboration_tool(
    tool_name: str,
    args: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Call one collaboration tool and return ``(result, is_error)``."""
    binding = await build_collaboration_runtime_binding_from_env(env)
    try:
        return await dispatch_collaboration_tool_bound(tool_name, args, binding)
    finally:
        if binding.owns_store and binding.store is not None:
            await binding.store.close()


async def dispatch_collaboration_tool_bound(
    tool_name: str,
    args: dict[str, Any],
    binding: CollaborationRuntimeBinding,
) -> tuple[dict[str, Any], bool]:
    """Call one collaboration tool using an already-bound runtime/store."""
    name = str(tool_name or "").strip()
    handler = BOUND_HANDLERS.get(name)
    if handler is None:
        return await _attach_mailbox_notice_bound({"error": f"unknown tool: {name}"}, binding), True

    allowed = binding.allowed_tools
    if allowed is None:
        allowed = allowed_tool_names(
            task=binding.context.task,
            context=binding.context,
            manager=binding.manager,
            env=binding.env,
        )

    if name not in allowed:
        return (
            await _attach_mailbox_notice_bound(
                {
                    "error": (
                        f"tool `{name}` is not available for this run. "
                        f"Allowed tools: {', '.join(sorted(allowed)) or '(none)'}."
                    )
                },
                binding,
            ),
            True,
        )

    try:
        result = await handler(dict(args or {}), binding)
    except Exception as exc:
        payload = (
            infrastructure_error_payload(exc, tool_name=name)
            if _is_infrastructure_error_text(exc)
            else {"error": str(exc)}
        )
        return await _attach_mailbox_notice_bound(payload, binding), True
    normalized = result if isinstance(result, dict) else {"result": result}
    if "error" in normalized and _is_infrastructure_error_text(normalized.get("error")):
        normalized = {**normalized, **infrastructure_error_payload(normalized.get("error"), tool_name=name)}
    normalized = await _attach_mailbox_notice_bound(normalized, binding)
    return normalized, bool("error" in normalized)


# Backward-compatible names for tests and small internal call sites that used
# the old module-local helper spellings.
_build_runtime = build_collaboration_runtime
_allowed_tool_names = allowed_tool_names
