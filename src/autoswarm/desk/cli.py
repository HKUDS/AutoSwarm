"""The `autoswarm desk ...` commands: AutoSwarm Desk's identity, private assistant and the
conversations started from it. The core CLI only registers them; plugin use never needs them."""
from __future__ import annotations

import json

# Commands whose message may follow its flags as one trailing argument (see cli.parse_args).
PROMPT_COMMANDS = {"assistant", "start"}


def register(commands) -> None:
    desk = commands.add_parser("desk", help="Desk identity and private assistant")
    desk_commands = desk.add_subparsers(dest="desk_command", required=True)
    desk_commands.add_parser("init", help="Initialize or restore the stable Desk controller")
    item = desk_commands.add_parser("status", help="Read the assistant binding without changing records")
    item.add_argument("--agent", help="The assistant conversation on this agent (default: the latest)")
    item = desk_commands.add_parser("transcript", help="Read one of this Desk's assistant conversations: its latest exchanges")
    item.add_argument("target", help="agent_id of the assistant conversation")
    item.add_argument("--limit", type=int, default=20, help="How many latest exchanges (default 20, at most 200)")
    desk_commands.add_parser("flush", help="Deliver queued human input when the bound native conversation is idle")
    item = desk_commands.add_parser("message", help="Send human input to a retained main or worker conversation")
    item.add_argument("target")
    item.add_argument("prompt", nargs="?")
    item.add_argument("--input")
    item.add_argument("--mode", choices=["after_turn", "immediate"], default="after_turn")
    item = desk_commands.add_parser("assistant", help="Start or continue the private assistant in the background")
    item.add_argument("prompt", nargs="?")
    item.add_argument("--input", help="UTF-8 user message file; '-' reads stdin")
    item.add_argument("--agent", help="The assistant's agent: its conversation on this agent continues, or one starts (default: the latest, else claude-code)")
    item.add_argument("--cwd", help="Workspace of a new assistant conversation; an existing one keeps its own")
    item.add_argument("--model", help="Native model ID; omitted preserves native defaults")
    item.add_argument("--effort", help="Native reasoning effort; omitted preserves native defaults")
    item.add_argument("--permissions", type=json.loads, help="Desk's default permission mode per agent, JSON {agent: mode}; it applies to this conversation and the workers it starts")
    item = desk_commands.add_parser("start", help="Start a main conversation directly under the Desk identity")
    item.add_argument("prompt", nargs="?")
    item.add_argument("--input", help="UTF-8 user message file; '-' reads stdin")
    item.add_argument("--agent", required=True, help="Provider of the main agent")
    item.add_argument("--cwd", required=True, help="Workspace of the new conversation")
    item.add_argument("--model", help="Native model ID; omitted preserves native defaults")
    item.add_argument("--effort", help="Native reasoning effort; omitted preserves native defaults")
    item.add_argument("--skill", choices=["on", "off"], help="on mentions the installed autoswarm Skill in the first turn so the agent loads it; off hides it (Claude Code, Codex)")
    item.add_argument("--permissions", type=json.loads, help="Desk's default permission mode per agent, JSON {agent: mode}; it applies to this conversation and the workers it starts")
    item.add_argument("--permission", help="This conversation's own mode, overriding its agent's default")
    item = desk_commands.add_parser("permission", help="Change a Desk conversation's permission mode from its next turn")
    item.add_argument("target", help="agent_id")
    item.add_argument("mode", help="One of the agent's own modes, e.g. acceptEdits, ask, allowlist")
    item = desk_commands.add_parser("cancel", help="Stop a conversation's running turn and the running calls beneath it")
    item.add_argument("target", help="agent_id or call_id")


def dispatch(args, records, prompt_from):
    from . import control
    if args.desk_command == "init":
        return control.initialize(records, args.controller)
    if args.desk_command == "status":
        return control.status(records, args.controller, args.agent)
    if args.desk_command == "transcript":
        return control.transcript(records, args.controller, args.target, args.limit)
    if args.desk_command == "message":
        return control.message(records, args.controller, args.target, prompt_from(args), mode=args.mode)
    if args.desk_command == "flush":
        return control.flush(records, args.controller)
    if args.desk_command == "start":
        return control.start(records, args.controller, prompt_from(args), provider=args.agent,
                             workspace=args.cwd, model=args.model, effort=args.effort, skill=None if args.skill is None else args.skill == "on",
                             permission=args.permission, permissions=args.permissions)
    if args.desk_command == "permission":
        return control.permission(records, args.controller, args.target, args.mode)
    if args.desk_command == "cancel":
        return control.cancel(records, args.controller, args.target)
    return control.assistant(records, args.controller, prompt_from(args), provider=args.agent,
                             workspace=args.cwd, model=args.model, effort=args.effort, permissions=args.permissions)
