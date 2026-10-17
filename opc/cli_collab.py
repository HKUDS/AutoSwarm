"""``opc-collab`` CLI — company-mode collaboration surface for external agents.

External agents spawned by AutoSwarm (codex, claude code, opencode, cursor) talk
to the rest of the company via this CLI.

The CLI reads the collaboration environment contract (``OPC_COMMS_FROM``,
``OPC_PROJECT_DB_PATH``, ``OPC_TASK_ID`` / ``OPC_RUNTIME_TASK_ID``,
``OPC_WORK_ITEM_ID``, ``OPC_COMMS_PROJECT``, ``OPC_COMMS_SESSION``,
``OPC_WORKSPACE_ROOT``, ``OPC_COLLAB_PROFILE``, ``OPC_ALLOWED_COLLAB_TOOLS``)
and dispatches through :mod:`opc.layer4_tools.collaboration_dispatch`.

Usage::

    opc-collab <subcommand> [--args-json-file path] [--args-stdin] [--args-json JSON] [--set key=value]

Success path: stdout is the JSON result of the tool call, exit 0.
Error path: stdout still carries a JSON object with an ``error`` field,
stderr carries a short human message, exit 1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from opc.core.company_tools import COMPANY_ALL_COLLABORATION_TOOL_NAMES

# Import lazily so `opc-collab --help` is fast and doesn't drag in the full
# store/communication graph when the user is just listing subcommands.

TOOL_SUBCOMMANDS: tuple[str, ...] = COMPANY_ALL_COLLABORATION_TOOL_NAMES


def _parse_args_json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"opc-collab: --args-json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("opc-collab: --args-json must decode to an object")
    return parsed


def _windows_external_rpc_env_configured() -> bool:
    if os.name != "nt":
        return False
    from opc.layer4_tools.collaboration_rpc import rpc_env_configured

    return rpc_env_configured()


def _reject_unsafe_windows_rpc_args_if_needed(opts: argparse.Namespace) -> None:
    if not _windows_external_rpc_env_configured():
        return
    if getattr(opts, "args_json", ""):
        raise SystemExit(
            "opc-collab: --args-json is disabled on Windows for AutoSwarm-spawned "
            "external agents because command-line JSON can corrupt non-ASCII text; "
            "use --args-json-file with a UTF-8 file instead"
        )
    if getattr(opts, "args_stdin", False):
        raise SystemExit(
            "opc-collab: --args-stdin is disabled on Windows for AutoSwarm-spawned "
            "external agents because shell pipelines can corrupt non-ASCII text; "
            "use --args-json-file with a UTF-8 file instead"
        )


def _parse_kv_arg(raw: str) -> tuple[str, Any]:
    """Parse a ``key=value`` pair.

    The value is interpreted as JSON first (so numbers, booleans, nested
    objects and arrays are usable), falling back to the raw string.
    """
    if "=" not in raw:
        raise SystemExit(f"opc-collab: expected key=value, got {raw!r}")
    key, _, value = raw.partition("=")
    key = key.strip()
    if not key:
        raise SystemExit(f"opc-collab: empty key in {raw!r}")
    stripped = value.strip()
    if stripped == "":
        return key, ""
    try:
        return key, json.loads(stripped)
    except json.JSONDecodeError:
        return key, value


def _collect_tool_args(opts: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    _reject_unsafe_windows_rpc_args_if_needed(opts)
    if opts.args_json_file:
        raw = Path(opts.args_json_file).expanduser().read_text(encoding="utf-8-sig")
        if raw.strip():
            payload.update(_parse_args_json(raw))
    if opts.args_stdin:
        raw = sys.stdin.read()
        if raw.strip():
            payload.update(_parse_args_json(raw))
    if opts.args_json:
        payload.update(_parse_args_json(opts.args_json))
    for raw_kv in opts.set or []:
        key, value = _parse_kv_arg(raw_kv)
        payload[key] = value
    return payload


async def _dispatch(tool_name: str, tool_args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Call the named collaboration tool and return (result, is_error).

    Kept as a small wrapper so CLI behavior stays stable while the runtime
    implementation lives in the neutral dispatch module.
    """
    from opc.layer4_tools.collaboration_rpc import (
        call_collaboration_rpc,
        rpc_env_available,
        rpc_env_configured,
    )

    if rpc_env_available() or rpc_env_configured():
        return await call_collaboration_rpc(tool_name, tool_args)

    from opc.layer4_tools.collaboration_dispatch import dispatch_collaboration_tool

    return await dispatch_collaboration_tool(tool_name, tool_args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opc-collab",
        description=(
            "Call AutoSwarm company-mode collaboration tools from an external "
            "agent spawned by AutoSwarm. Output is JSON on stdout."
        ),
    )
    subparsers = parser.add_subparsers(dest="tool", required=True, metavar="<tool>")

    # One subparser per tool. Arg plumbing is identical across subcommands;
    # we drive behavior purely by the subcommand name.
    for name in TOOL_SUBCOMMANDS:
        sub = subparsers.add_parser(
            name,
            help=f"Invoke the `{name}` collaboration tool",
            description=(
                f"Invoke the AutoSwarm collaboration tool `{name}`. Arguments come from "
                "--args-json-file, --args-stdin, or --args-json and may be "
                "overridden by key=value pairs supplied via --set."
            ),
        )
        sub.add_argument(
            "--args-json",
            default="",
            help=(
                "Inline JSON object with the tool arguments. Prefer --args-json-file "
                "or --args-stdin; inline JSON is disabled for Windows external-agent RPC runs."
            ),
        )
        sub.add_argument(
            "--args-json-file",
            default="",
            help="Read a JSON argument object from a UTF-8 file.",
        )
        sub.add_argument(
            "--args-stdin",
            action="store_true",
            help=(
                "Read a JSON argument object from stdin. Disabled for Windows "
                "external-agent RPC runs; use --args-json-file with a UTF-8 file there."
            ),
        )
        sub.add_argument(
            "--set",
            action="append",
            metavar="key=value",
            help="Override an argument. Value is parsed as JSON when valid, else as raw string.",
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    opts = parser.parse_args(argv)
    tool_args = _collect_tool_args(opts)

    try:
        result, is_error = asyncio.run(_dispatch(opts.tool, tool_args))
    except KeyboardInterrupt:
        sys.stderr.write("opc-collab: interrupted\n")
        return 130

    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()
    if is_error:
        message = str(result.get("error") if isinstance(result, dict) else result)
        sys.stderr.write(f"opc-collab[{opts.tool}] error: {message}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
