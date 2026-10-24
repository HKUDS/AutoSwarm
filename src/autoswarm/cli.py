"""A shared CLI for people, skills, detached runners and host hooks."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, engine, approvals, messages
from .hosts import deliver, detect_parent
from .providers import BINARIES, discover
from .records import Records, SwarmError, read_json, write_json


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="autoswarm", description="Use your installed coding agents together from one conversation.")
    root.add_argument("--version", action="version", version=f"AutoSwarm {__version__}")
    root.add_argument("--home", help="Records/config directory (or AUTOSWARM_HOME)")
    root.add_argument("--controller", help="Retained controller_id for hosts without a native session identity")
    commands = root.add_subparsers(dest="command")
    for name in ("agents", "doctor"):
        item = commands.add_parser(name)
        item.add_argument("--json", action="store_true", help="JSON is also the default")
        if name == "doctor":
            item.add_argument("--agent", action="append", help="Provider to check; repeat to select several")
            item.add_argument("--probe", action="store_true", help="Spend model calls to verify invocation and native resume")
            item.add_argument("--cwd", default=os.getcwd(), help="Native project/config context for the probe")
    for name in ("run", "resume"):
        item = commands.add_parser(name)
        if name == "run":
            item.add_argument("--agent", required=True)
            item.add_argument("--cwd", "--directory", "-C", default=os.getcwd())
            item.add_argument("--name", help="Conversation name, unique under this assigning parent")
            item.add_argument("--allow-peer", action="append", default=None, help="Parent-authorized peer name/agent ID; repeat to allow several")
            item.add_argument("--communication-rules", help="Concise communication instructions from the assigning parent")
        else:
            item.add_argument("call_id")
        item.add_argument("prompt", nargs="?")
        item.add_argument("--input", help="UTF-8 prompt file; '-' reads stdin")
        item.add_argument("--background", action="store_true")
        item.add_argument("--notify-parent", action="store_true", help="Opt into callbacks through the original host binding; background execution alone uses wait/receive")
        item.add_argument("--model", help="Native model ID for this child; omitted preserves native defaults")
        item.add_argument("--effort", help="Native reasoning effort/variant for this child; omitted preserves native defaults")
        item.add_argument("--provider-arg", action="append", default=None, help="Native argument; use --provider-arg=--flag")
        item.add_argument("--delegate-approvals", choices=["on", "off"], help="Override parent approval delegation for this child (default: config, on)")
        group = item.add_mutually_exclusive_group()
        group.add_argument("--parent", help="JSON binding to the original host session")
        group.add_argument("--no-parent", action="store_true")
    for name in ("wait", "receive"):
        item = commands.add_parser(name)
        item.add_argument("call_ids", nargs="*")
        item.add_argument("--message", help="Wait for or receive a reply to this message")
        if name == "wait":
            item.add_argument("--any", action="store_true", dest="any_result")
            item.add_argument("--peek", action="store_true", help="Read without acknowledging receipt; used by native host integrations")
            item.add_argument("--unseen", action="store_true", help="Wait for a native request not yet delivered, or the final result")
            item.add_argument("--call-events-only", action="store_true", help=argparse.SUPPRESS)
    for name in ("send", "reply"):
        item = commands.add_parser(name)
        item.add_argument("target", help="Peer name, agent ID or parent; reply takes a message ID")
        item.add_argument("prompt", nargs="?")
        item.add_argument("--input", help="UTF-8 message file; '-' reads stdin")
        if name == "send":
            item.add_argument("--interrupt", action="store_true", help="Request priority handling; use native live input first, otherwise interrupt the running turn")
    for name in ("cancel", "inspect", "deliver"):
        item = commands.add_parser(name)
        item.add_argument("call_id")
        if name == "deliver":
            item.add_argument("--request", help="Redeliver a pending native request instead of the final result")
    item = commands.add_parser("config")
    item.add_argument("--delegate-approvals", choices=["on", "off"], help="Persist the default parent delegation setting; enabled by default")
    item = commands.add_parser("approvals")
    item.add_argument("call_id", nargs="?")
    for name in ("approve", "deny"):
        item = commands.add_parser(name)
        item.add_argument("call_id")
        item.add_argument("request_id")
        item.add_argument("--by", choices=["parent", "user"], default="parent", dest="actor", help="user relays an explicit human decision; it is not permission to impersonate one")
        item.add_argument("--scope", choices=["once", "session"], default="once", help="Native grant scope, when supported")
        item.add_argument("--message", default="")
    item = commands.add_parser("answer", help="Answer a child's native question, confirmation or form")
    item.add_argument("call_id")
    item.add_argument("request_id")
    item.add_argument("prompt", nargs="?", help="Native response JSON; --input avoids shell quoting")
    item.add_argument("--input", help="UTF-8 JSON response file; '-' reads stdin")
    item.add_argument("--by", choices=["parent", "user"], default="parent", dest="actor")
    item = commands.add_parser("calls")
    item.add_argument("--pending-return", action="store_true")
    item.add_argument("--host")
    item.add_argument("--parent-workspace")
    item.add_argument("--parent-session")
    item.add_argument("--parent-instance")
    item = commands.add_parser("bind")
    item.add_argument("--host", choices=["codex", "claude-code", "opencode", "command"], required=True)
    item.add_argument("--session", required=True)
    item.add_argument("--endpoint")
    item.add_argument("--mode", choices=["native", "hook"], default="native")
    item.add_argument("--output", required=True)
    item.add_argument("--bridge-argv", help="JSON argv array for a custom bridge")
    item.add_argument("--token-env")
    item.add_argument("--workspace")
    item = commands.add_parser("setup")
    item.add_argument("--host", choices=list(BINARIES), required=True)
    item.add_argument("--skill-dir")
    item.add_argument("--settings-file", help="Claude settings.json; merged with existing settings")
    item.add_argument("--plugin-dir", help="OpenCode plugin destination")
    item = commands.add_parser("hook")
    item.add_argument("--host", choices=["claude-code"], required=True)
    item.add_argument("--event", choices=["session-start", "post-tool", "stop"], required=True)
    commands.add_parser("_runner", help=argparse.SUPPRESS).add_argument("call_id")
    commands.add_parser("_message", help=argparse.SUPPRESS).add_argument("message_id")
    item = commands.add_parser("_receipt", help=argparse.SUPPRESS)
    item.add_argument("call_id")
    item.add_argument("--session", required=True)
    item.add_argument("--message", required=True)
    item.add_argument("--request")
    item.add_argument("--issue", help=argparse.SUPPRESS)
    return root


def prompt_from(args) -> str:
    if args.input and args.prompt is not None:
        raise SwarmError("invalid_input", "Use a prompt or --input, not both")
    if args.input and args.input != "-":
        value = Path(args.input).read_text(encoding="utf-8")
    elif args.input == "-" or (args.prompt is None and not sys.stdin.isatty()):
        value = sys.stdin.read()
    else:
        value = args.prompt or ""
    if not value.strip():
        raise SwarmError("invalid_input", "Provide a prompt or --input file")
    return value


def dispatch(args, records: Records):
    name = args.command
    if name in {"agents", "doctor"}:
        value = {"agents": discover(records.config(), args.agent if name == "doctor" else None), "home": str(records.home)}
        if name == "doctor":
            if args.probe:
                if not args.agent:
                    raise SwarmError("invalid_input", "Select the providers to test with --agent; --probe spends model calls")
                from .doctor import probe
                value["probes"] = [probe(records, agent["provider"], args.cwd) for agent in value["agents"]]
                value["verified"] = bool(value["probes"]) and all(result["verified"] for result in value["probes"])
                for agent, result in zip(value["agents"], value["probes"]):
                    agent["invocation_verified"] = result["verified"]
            value["parent"] = detect_parent()
            value["note"] = "Executable checks do not spend model calls or prove authentication. Native return requires a host binding or installed lifecycle integration."
        return value
    if name in {"run", "resume"}:
        prompt = prompt_from(args)
        parent = engine.parent_from_options(args.parent, args.no_parent)
        callback_parent = parent
        if name == "resume" and parent is None and not args.no_parent:
            callback_parent = records.require(args.call_id)[1].get("parent")
        if args.notify_parent and not args.background:
            raise SwarmError("invalid_input", "Use --notify-parent with --background, or collect the foreground result directly")
        if args.notify_parent and (not callback_parent or (callback_parent.get("mode") != "hook" and not callback_parent.get("endpoint") and callback_parent.get("host") != "command")):
            raise SwarmError("parent_endpoint_required", "--notify-parent needs a usable original host binding. Omit it and collect results with wait/receive.")
        common = {"parent": parent, "model": args.model, "effort": args.effort, "provider_args": args.provider_arg, "return_mode": "native" if args.notify_parent else "wait", "delegate_approvals": None if args.delegate_approvals is None else args.delegate_approvals == "on"}
        info = engine.start_call(records, args.agent, prompt, args.cwd, controller=args.controller, name=args.name, peers=args.allow_peer, rules=args.communication_rules, **common) if name == "run" else engine.resume(records, args.call_id, prompt, inherit_parent=not args.no_parent, **common)
        if args.background:
            return info
        values = engine.wait(records, [info["call_id"]], controller=info["controller_id"])
        return values[0] if len(values) == 1 else values
    if name in {"send", "reply"}:
        sender = messages.identity(records, args.controller)
        text = prompt_from(args)
        return messages.send(records, args.target, text, sender=sender, interrupt=args.interrupt) if name == "send" else messages.reply(records, args.target, text, sender=sender)
    if name in {"wait", "receive"} and args.message:
        if args.call_ids:
            raise SwarmError("invalid_input", "Select call IDs or --message")
        return messages.wait(records, args.message, sender=messages.identity(records, args.controller), block=name == "wait", mark=not getattr(args, "peek", False))
    if name == "wait":
        return engine.wait(records, args.call_ids, any_result=args.any_result, mark_received=not args.peek, unseen=args.unseen, controller=args.controller, include_inbox=not args.call_events_only)
    if name == "receive":
        return engine.receive(records, args.call_ids, controller=args.controller)
    if name == "inspect":
        if args.call_id.startswith("msg_"):
            return messages.inspect(records, args.call_id)
        return engine.inspect_call(records, args.call_id)
    if name == "cancel":
        return engine.cancel(records, args.call_id)
    if name == "deliver":
        if args.call_id.startswith("msg_"):
            return messages.retry(records, args.call_id, sender=messages.identity(records, args.controller))
        return deliver(records, args.call_id, explicit=True, request_id=args.request)
    if name == "config":
        return approvals.configure(records, None if args.delegate_approvals is None else args.delegate_approvals == "on")
    if name == "_message":
        return messages.deliver_to_host(records, args.message_id)
    if name == "approvals":
        ids = [args.call_id] if args.call_id else [task["call_id"] for _, task in records.calls()]
        return [request for call_id in ids for request in approvals.pending(records, call_id)]
    if name == "answer":
        try:
            response = json.loads(prompt_from(args))
        except ValueError as error:
            raise SwarmError("invalid_answer", "Answer must be valid JSON: " + str(error)) from error
        return approvals.answer(records, args.call_id, args.request_id, response, actor=args.actor)
    if name in {"approve", "deny"}:
        return approvals.decide(records, args.call_id, args.request_id, "allow" if name == "approve" else "deny", actor=args.actor, scope=args.scope, message=args.message)
    if name == "_receipt":
        from .records import now
        from .platform import session_lease
        directory, task = records.require(args.call_id)
        parent = task.get("parent") or {}
        if parent.get("host") != "opencode" or parent.get("session_id") != args.session:
            raise SwarmError("parent_mismatch", "Native receipt does not match this call's original parent")
        if args.request:
            directory = approvals.location(records, args.call_id, args.request)
        if args.issue:
            value = {"diagnostic_at": args.issue, "at": now()}
            write_json(directory / "communication-received.json", value)
            return value
        lease = session_lease(directory / "delivery.lock")
        try:
            value = {"state": "delivered", "continued": True, "mechanism": "opencode_plugin", "message_id": args.message, "call_id": args.call_id, "at": now()}
            if args.request:
                value["request_id"] = args.request
            write_json(directory / "delivery.json", value)
            return value
        finally:
            lease.close()
    if name == "calls":
        values = []
        for directory, task in records.calls():
            parent = task.get("parent") or {}
            if args.host and parent.get("host") != args.host:
                continue
            if args.parent_workspace and parent.get("workspace") != args.parent_workspace:
                continue
            if args.parent_session and parent.get("session_id") != args.parent_session:
                continue
            if args.parent_instance and parent.get("host_instance") != args.parent_instance:
                continue
            if args.pending_return and (not parent or task.get("return_mode") == "wait" or (directory / "received.json").exists() or read_json(directory / "delivery.json", {}).get("state") == "delivered"):
                continue
            values.append(engine.inspect_call(records, task["call_id"]))
        return values
    if name == "bind":
        parent = {"host": args.host, "session_id": args.session, "mode": args.mode}
        for field in ("endpoint", "token_env", "workspace"):
            if getattr(args, field):
                parent[field] = getattr(args, field)
        if args.bridge_argv:
            parent["command"] = json.loads(args.bridge_argv)
        if args.host in {"codex", "opencode"} and args.mode == "native" and not args.endpoint:
            raise SwarmError("parent_endpoint_required", "Bind the original host endpoint; do not start a separate replacement host")
        write_json(Path(args.output).expanduser(), parent)
        return {"parent": parent, "file": str(Path(args.output).expanduser().resolve())}
    if name == "setup":
        from .setup import install
        return install(records, args.host, args.skill_dir, args.settings_file, args.plugin_dir)
    raise SwarmError("unknown_command", str(name))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    root = parser()
    args, remaining = root.parse_known_args(argv)
    # Python 3.10 argparse can leave an optional trailing positional behind
    # when flags occur after the required call ID. Preserve the same CLI
    # ordering accepted by newer Python versions without accepting extra flags.
    if args.command in {"run", "resume", "send", "reply"} and args.prompt is None and len(remaining) == 1 and not remaining[0].startswith("-"):
        args.prompt = remaining.pop()
    if remaining:
        root.error("unrecognized arguments: " + " ".join(remaining))
    return args


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.command is None:
        parser().print_help()
        return
    records = Records(args.home)
    if args.command == "_runner":
        from .runner import execute
        raise SystemExit(execute(records, args.call_id))
    if args.command == "hook":
        from .hooks import handle_hook
        raise SystemExit(handle_hook(records, args.event))
    try:
        value = dispatch(args, records)
        print(json.dumps({"ok": True, "data": value, "error": None}, ensure_ascii=False))
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": {"code": "wait_interrupted", "message": "Waiting interrupted; background calls remain available. Use cancel to stop a call."}}), file=sys.stderr)
        raise SystemExit(130)
    except (SwarmError, OSError, ValueError) as error:
        print(json.dumps({"ok": False, "data": None, "error": {"code": getattr(error, "code", "operation_failed"), "message": str(error)}}, ensure_ascii=False))
        raise SystemExit(1)
