"""Launch native agents with a bidirectional permission-capable transport."""
from __future__ import annotations

import shutil
import socket
import subprocess

from .records import SwarmError


ALIASES = {
    "claude": "claude-code", "claude_code": "claude-code", "cursor-agent": "cursor",
    "github-copilot": "copilot", "muse-code": "muse", "meta-muse-code": "muse", "codebuddy-code": "codebuddy",
}
BINARIES = {
    "codex": "codex", "claude-code": "claude", "opencode": "opencode",
    "cursor": "cursor-agent", "copilot": "copilot", "muse": "muse", "codebuddy": "codebuddy",
}
ACP_PROVIDERS = {"cursor", "copilot", "codebuddy"}
OWNED_SERVERS = ACP_PROVIDERS | {"opencode", "muse"}


def canonical(name: str) -> str:
    name = ALIASES.get(name, name)
    if name not in BINARIES:
        raise SwarmError("unknown_provider", f"Unknown provider: {name}")
    return name


def command_for(name: str, config: dict) -> list[str]:
    name = canonical(name)
    configured = config.get("providers", {}).get(name, {}).get("command", [BINARIES[name]])
    if not isinstance(configured, list) or not configured or not all(isinstance(x, str) for x in configured):
        raise SwarmError("invalid_config", "Provider command must be an argv array")
    binary = shutil.which(configured[0])
    if not binary:
        raise SwarmError("provider_unavailable", f"No callable CLI for {name}: {configured[0]}. Check the configured path/PATH; if no CLI is installed, ask the user whether to install its official CLI.")
    return [binary, *configured[1:]]


def invocation(task: dict, config: dict) -> list[str]:
    name = canonical(task["provider"])
    command = command_for(name, config)
    extras = []
    for extra in (config.get("providers", {}).get(name, {}).get("args", []), task.get("provider_args", [])):
        if not isinstance(extra, list) or not all(isinstance(value, str) for value in extra):
            raise SwarmError("invalid_config", "Provider args must be an array of strings")
        extras += extra
    options = {}
    native_args = []
    index = 0
    mappings = ({"--sandbox": "sandbox", "-s": "sandbox", "--ask-for-approval": "approvalPolicy", "-a": "approvalPolicy", "--model": "model", "-m": "model"}
                if name == "codex" else {"--model": "model", "-m": "model", "--agent": "agent", "--variant": "variant", "--title": "title"} if name == "opencode" else {})
    while index < len(extras):
        flag, sep, inline = extras[index].partition("=")
        if name == "opencode" and flag in {"--port", "--hostname"}:
            raise SwarmError("invalid_config", "AutoSwarm owns the OpenCode service address; remove --port/--hostname from provider args")
        if flag in mappings:
            if not sep and index + 1 >= len(extras):
                raise SwarmError("invalid_config", f"Missing native argument value: {flag}")
            options[mappings[flag]] = inline if sep else extras[index + 1]
            index += 1 if sep else 2
        elif name == "codex" and flag == "--full-auto":
            options.update({"sandbox": "workspace-write", "approvalPolicy": "on-request"})
            index += 1
        else:
            native_args.append(extras[index])
            index += 1
    if task.get("model"):
        options["model"] = task["model"]
    task["native_options"] = options
    if name == "codex":
        command += ["app-server"]
    elif name == "claude-code":
        command += ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--permission-prompt-tool", "stdio"]
        if task.get("native_session_id"):
            command += ["--resume=" + task["native_session_id"]]
        if task.get("model"):
            command += ["--model", task["model"]]
    elif name in ACP_PROVIDERS:
        if task.get("model"):
            command += ["--model", task["model"]]
        command += native_args + (["acp"] if name == "cursor" else ["--acp"])
        return command
    elif name == "muse":
        command += ["serve"]
        if task.get("model"):
            command += ["--model", task["model"]]
    else:
        # A known, OS-selected endpoint removes dependence on startup wording
        # or whether the native CLI sends its banner to stdout or stderr.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        task["native_endpoint"] = f"http://127.0.0.1:{port}"
        command += ["serve", "--hostname", "127.0.0.1", "--port", str(port)]
    command += native_args
    return command


def discover(config: dict, providers=None) -> list[dict]:
    found = []
    for name in dict.fromkeys(canonical(value) for value in (providers if providers is not None else BINARIES)):
        try:
            command = command_for(name, config)
            info = subprocess.run([*command, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
            value = {"provider": name, "available": info.returncode == 0, "check": "executable", "command": command, "version": info.stdout.strip(), "invocation_verified": False, "session_resume": True, "approval_relay": True}
            if info.returncode:
                value["error"] = info.stderr.strip() or info.stdout.strip() or f"CLI version check exited with code {info.returncode}"
            found.append(value)
        except (SwarmError, OSError) as error:
            found.append({"provider": name, "available": False, "check": "executable", "invocation_verified": False, "error": str(error)})
    return found
