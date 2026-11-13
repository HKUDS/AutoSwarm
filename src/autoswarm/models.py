"""Model and reasoning-effort choices each agent reports itself (no model call).

Each agent is asked, so the list follows however the user set it up: an official
login lists that account's models, a provider of the user's own (an API) lists
the models configured for it.

- Claude Code: the models its stream-json initialize reports, each with its
  effort levels (older versions: the --effort levels and aliases in --help).
- Codex: the app-server's model/list, and config/read for the configured model,
  effort and provider. A model provider of the user's own offers its configured
  model rather than OpenAI's list.
- Cursor: `--list-models`; the effort is part of the model id (grok-4.7-high).
- ACP agents (Copilot, CodeBuddy, Hermes, Kimi, DSH, Grok, Nanobot, OpenCode 2):
  what a new session advertises, with per-model effort levels where the agent
  gives them. The empty session is deleted (or closed) when the agent allows it.
Muse and OpenCode 1 have no catalog; Desk then offers free text, passed through as is.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import platform
from .providers import ACP_PROVIDERS, canonical, command_for
from .records import SwarmError

TIMEOUT = 40
ACP_TIMEOUT = 90  # CodeBuddy and Hermes take tens of seconds to open a session.
IDENTITY = ("AUTOSWARM_CALL_ID", "AUTOSWARM_PARENT", "CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_SESSION_ID")


def _run(argv):
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SwarmError("catalog_unavailable", f"Could not ask the agent for its models: {error}") from error
    return done.stdout + "\n" + done.stderr


class _Lines:
    """A short-lived JSON-lines conversation with an agent process."""

    def __init__(self, argv, *, timeout=TIMEOUT, env=None, cwd=None, keepalive=None):
        environment = {key: value for key, value in (env or os.environ).items() if key not in IDENTITY}
        try:
            self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            cwd=cwd, env=environment, **platform.detached_options())
        except OSError as error:
            raise SwarmError("catalog_unavailable", f"Could not start the agent to ask for its models: {error}") from error
        self.deadline = time.monotonic() + timeout
        self.lines, self.errors, self.lock = queue.Queue(), [], threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [self.errors.append(raw.decode("utf-8", "replace")) for raw in self.process.stderr], daemon=True).start()
        if keepalive:
            threading.Thread(target=self._keepalive, args=(keepalive,), daemon=True).start()

    def _read(self):
        for raw in self.process.stdout:
            try:
                value = json.loads(raw)
            except ValueError:
                continue
            if isinstance(value, dict):
                self.lines.put(value)
        self.lines.put(None)

    def _keepalive(self, value):
        while self.process.poll() is None:
            time.sleep(1)
            try:
                self.send(value)
            except (OSError, ValueError):
                return

    def send(self, value):
        with self.lock:
            self.process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
            self.process.stdin.flush()

    def wait(self, match, *, on_other=None):
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise SwarmError("catalog_unavailable", "The agent did not list its models in time" + self._tail())
            try:
                value = self.lines.get(timeout=min(remaining, 1))
            except queue.Empty:
                continue
            if value is None:
                raise SwarmError("catalog_unavailable", "The agent closed before listing its models" + self._tail())
            if match(value):
                return value
            if on_other:
                on_other(value)

    def _tail(self):
        text = "".join(self.errors).strip()
        return (": " + text[-600:]) if text else ""

    def close(self):
        try:
            platform.stop_child(self.process)
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _rpc(lines, request_id, method, params, *, reply_to_requests=False):
    """Send one JSON-RPC request and return its result (requests from the agent are declined)."""
    def other(value):
        if reply_to_requests and "method" in value and "id" in value:
            lines.send({"jsonrpc": "2.0", "id": value["id"], "error": {"code": -32601, "message": "Not available while listing models"}})
    lines.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params} if reply_to_requests else {"id": request_id, "method": method, "params": params})
    value = lines.wait(lambda v: v.get("id") == request_id and "method" not in v, on_other=other)
    if "error" in value:
        error = value["error"] if isinstance(value["error"], dict) else {"message": str(value["error"])}
        code = "native_auth_required" if "auth" in str(error.get("message", "")).lower() else "catalog_unavailable"
        raise SwarmError(code, f"{method}: {error.get('message', error)}")
    return value.get("result") or {}


# --- Codex ------------------------------------------------------------------------------

def _codex(command):
    with _Lines([*command, "app-server"]) as lines:
        _rpc(lines, 1, "initialize", {"clientInfo": {"name": "autoswarm-models", "version": "1"}})
        lines.send({"method": "initialized", "params": {}})
        models, cursor = [], None
        for request_id in range(2, 12):  # model/list is paginated.
            result = _rpc(lines, request_id, "model/list", {"cursor": cursor} if cursor else {})
            models += result.get("data") or []
            cursor = result.get("nextCursor")
            if not cursor:
                break
        try:
            configured = _rpc(lines, 20, "config/read", {}).get("config") or {}
        except SwarmError:
            configured = {}  # Older app-servers: the list alone.
    listed = []
    for model in models:
        if not isinstance(model, dict) or model.get("hidden"):
            continue
        efforts = [e.get("reasoningEffort") for e in model.get("supportedReasoningEfforts") or [] if isinstance(e, dict) and e.get("reasoningEffort")]
        listed.append({"id": model.get("model") or model.get("id"), "label": model.get("displayName") or model.get("model") or model.get("id"),
                       "efforts": efforts, "default_effort": model.get("defaultReasoningEffort"), "default": bool(model.get("isDefault"))})
    return _codex_configured(listed, configured)


def _codex_configured(listed, configured):
    """What runs when Desk passes no model is the configured one: it is the default, with its effort.

    A provider of the user's own (model_provider other than openai) is reached with the
    configured model only; OpenAI's list would go to that provider, so it is not offered.
    """
    model, effort = configured.get("model"), configured.get("model_reasoning_effort")
    provider = configured.get("model_provider")
    every = list(dict.fromkeys(e for m in listed for e in m["efforts"]))
    if provider and provider != "openai":
        name = ((configured.get("model_providers") or {}).get(provider) or {}).get("name") or provider
        if not model:
            return {"models": [], "efforts": every}
        return {"models": [{"id": model, "label": model, "description": name, "efforts": [], "default": True,
                            **({"default_effort": effort} if effort else {})}], "efforts": every}
    if model:
        found = next((m for m in listed if m["id"] == model), None)
        if not found:
            found = {"id": model, "label": model, "efforts": every, "default": False}
            listed.insert(0, found)
        for item in listed:
            item["default"] = item is found
        if effort and (effort in found["efforts"] or not found["efforts"]):
            found["default_effort"] = effort
    return {"models": listed, "efforts": []}


# --- Cursor -----------------------------------------------------------------------------

def _cursor(command):
    text = _run([*command, "--list-models"])
    listed = []
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z0-9][\w.\-\[\]=,]*)\s+-\s+(.+?)\s*$", line)
        if not match:
            continue
        label = re.sub(r"[​-‍﻿]", "", match.group(2))
        default = bool(re.search(r"\((?:[^)]*\b)?default\b[^)]*\)", label))
        label = re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()
        listed.append({"id": match.group(1), "label": re.sub(r"\s{2,}", " ", label), "efforts": [], "default": default})
    if not listed:
        raise SwarmError("catalog_unavailable", "Cursor listed no models (is it logged in?)")
    return {"models": _cursor_groups(listed), "efforts": []}


LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
LEVEL_WORDS = r"\s+(?:None|Minimal|Low|Medium|High|Extra High|Max)\b"


def _cursor_groups(listed):
    """grok-4.7-{low,medium,high,xhigh}[-fast] become one model whose effort
    picks the id ("variants"); Cursor still receives the full model id."""
    groups, order = {}, []
    for model in listed:
        match = re.fullmatch(r"(.+?)-(" + "|".join(LEVELS) + r")(-fast)?", model["id"])
        key = (match.group(1) + (match.group(3) or "")) if match else model["id"]
        if key not in groups:
            label = re.sub(LEVEL_WORDS, "", model["label"]).strip() if match else model["label"]
            groups[key] = {"id": key if not match else model["id"], "label": label, "efforts": [], "variants": {}, "default": False}
            order.append(key)
        group = groups[key]
        group["default"] = group["default"] or model["default"]
        if match:
            group["variants"][match.group(2)] = model["id"]
            group["efforts"] = [level for level in LEVELS if level in group["variants"]]
            group["default_effort"] = "high" if "high" in group["variants"] else group["efforts"][-1]
            group["id"] = group["variants"][group["default_effort"]]
    for key in order:
        if not groups[key]["variants"]:
            groups[key].pop("variants")
    return [groups[key] for key in order]


# --- Claude Code ------------------------------------------------------------------------

def _claude(command):
    """Claude Code's initialize lists the account's models, or those an API setup configures."""
    # Hooks would run for a session that never takes a prompt; this process only lists models.
    argv = [*command, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--settings", json.dumps({"disableAllHooks": True})]
    try:
        with _Lines(argv, cwd=tempfile.gettempdir()) as lines:
            lines.send({"type": "control_request", "request_id": "autoswarm_models", "request": {"subtype": "initialize"}})
            value = lines.wait(lambda v: v.get("type") == "control_response")
        response = (value.get("response") or {}).get("response") or {}
        models = response.get("models")
    except SwarmError:
        models = None
    if not isinstance(models, list) or not models:
        return _claude_help(command)
    raw = {m["value"]: m for m in models if isinstance(m, dict) and isinstance(m.get("value"), str) and m["value"]}
    # "default" is what Claude Code runs without --model; Desk's own default row stands for it,
    # and the model it resolves to is tagged as the default.
    resolved = (raw.get("default") or {}).get("resolvedModel")
    listed, every = [], []
    for value, model in raw.items():
        efforts = [e for e in model.get("supportedEffortLevels") or [] if isinstance(e, str)] if model.get("supportsEffort", True) else []
        every += [e for e in efforts if e not in every]
        if value == "default":
            continue
        listed.append({"id": value, "label": model.get("displayName") or value,
                       **({"description": model["description"]} if model.get("description") else {}),
                       "efforts": efforts, "default": bool(resolved) and model.get("resolvedModel") == resolved})
    return {"models": listed, "efforts": every}


def _claude_help(command):
    text = _run([*command, "--help"])
    flat = re.sub(r"\s+", " ", text)
    effort = re.search(r"--effort <level>.*?\(([a-z, ]+)\)", flat)
    efforts = [e.strip() for e in effort.group(1).split(",") if e.strip()] if effort else []
    model = re.search(r"--model <model>(.*?)(?: --[a-z])", flat)
    aliases = re.findall(r"'([a-z][a-z0-9-]*)'", model.group(1)) if model else []
    return {"models": [{"id": alias, "label": alias.capitalize(), "efforts": efforts, "default": False} for alias in aliases], "efforts": efforts}


# --- ACP agents -------------------------------------------------------------------------

EFFORT_IDS = {"thought_level", "reasoning_effort", "reasoningEffort", "effort", "reasoning", "thinking"}


def _flatten(options, group=None):
    for item in options or []:
        if not isinstance(item, dict):
            continue
        if "value" in item:
            yield item, group
        else:
            yield from _flatten(item.get("options"), item.get("name") or item.get("group"))


def _effort_list(meta):
    """Per-model levels: Grok's reasoningEfforts, CodeBuddy's effortOptions."""
    raw = meta.get("reasoningEfforts") or meta.get("effortOptions") or []
    values = [(e.get("value") or e.get("id"), e.get("label") or e.get("name")) for e in raw if isinstance(e, dict) and (e.get("value") or e.get("id"))]
    default = meta.get("reasoningEffort") or next((e.get("value") or e.get("id") for e in raw if isinstance(e, dict) and e.get("default")), None)
    return values, default


def acp_catalog(provider, result):
    """Turn what session/new advertised into Desk's catalog."""
    state = result.get("models") or {}
    configs = [c for c in result.get("configOptions") or [] if isinstance(c, dict)]
    selector = next((c for c in configs if c.get("id") == "model" or c.get("category") == "model"), None)
    effort = next((c for c in configs if c.get("category") in {"thought_level", "reasoning_effort"} or c.get("id") in EFFORT_IDS), None)
    advertised = {m["modelId"]: m for m in state.get("availableModels") or [] if isinstance(m, dict) and m.get("modelId")}
    current = state.get("currentModelId") or (selector or {}).get("currentValue")
    labels = {}
    general = []
    for item, _ in _flatten((effort or {}).get("options")):
        if item["value"] == "default":
            continue  # Desk's own default choice (no --effort) is the same thing.
        general.append(item["value"])
        if item.get("name"):
            labels[item["value"]] = item["name"]
    entries = []
    if selector:
        for item, group in _flatten(selector.get("options")):
            entries.append((item["value"], item.get("name") or item["value"], item.get("description") or group))
    else:
        entries = [(key, m.get("name") or key, m.get("description")) for key, m in advertised.items()]
    if current and current not in {key for key, _, _ in entries}:
        entries.insert(0, (current, (advertised.get(current) or {}).get("name") or current, None))
    listed = []
    for key, label, description in entries:
        meta = (advertised.get(key) or {}).get("_meta") or {}
        levels, default_effort = _effort_list(meta)
        for value, name in levels:
            if name:
                labels.setdefault(value, re.sub(r"\s+Effort$", "", name))
        efforts = [value for value, _ in levels if value != "default"]
        if key == current and not efforts and general:
            efforts, default_effort = list(general), (effort or {}).get("currentValue")
        if default_effort not in efforts:
            default_effort = None
        model = {"id": _dsh_id(key) if provider == "dsh" else key, "label": label, "efforts": efforts, "default": key == current}
        if default_effort:
            model["default_effort"] = default_effort
        if description and description != label:
            model["description"] = description
        listed.append(model)
    # Two entries with one name (CodeBuddy's Hy4 free/standard) differ by their description.
    names = [m["label"] for m in listed]
    for model in listed:
        if names.count(model["label"]) > 1 and model.get("description"):
            model["label"] += " · " + model["description"]
    return {"models": listed, "efforts": general, **({"effort_labels": labels} if labels else {})}


def _dsh_id(value):
    """DSH's selector values are JSON routes; provider/model selects the same route."""
    try:
        route = json.loads(value)
    except (TypeError, ValueError):
        return value
    return route[0] + "/" + route[1] if isinstance(route, list) and len(route) == 2 and all(isinstance(v, str) for v in route) else value


def _acp(provider, config):
    from .acp import KEEPALIVE, KEEPALIVE_LINE
    from .providers import invocation, opencode_environment
    # A scratch folder: the probe session is not one of the user's projects.
    workspace = Path(tempfile.gettempdir()) / "autoswarm-models"
    workspace.mkdir(exist_ok=True)
    task = {"provider": provider, "workspace": str(workspace), "prompt": ""}
    argv = invocation(task, config)
    if provider == "opencode" and task.get("native_transport") != "acp":
        return None
    env = opencode_environment(task, dict(os.environ))
    keepalive = {"jsonrpc": "2.0", **KEEPALIVE_LINE} if provider in KEEPALIVE else None
    with _Lines(argv, timeout=ACP_TIMEOUT, env=env, cwd=workspace, keepalive=keepalive) as lines:
        capabilities = {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
        if provider == "cursor":
            capabilities["_meta"] = {"parameterizedModelPicker": True}
        started = _rpc(lines, 1, "initialize", {"protocolVersion": 1, "clientInfo": {"name": "autoswarm-models", "version": "2.0.0.dev0"},
                                                "clientCapabilities": capabilities}, reply_to_requests=True)
        result = _rpc(lines, 2, "session/new", {"cwd": str(workspace), "mcpServers": []}, reply_to_requests=True)
        session = result.get("sessionId")
        offered = (started.get("agentCapabilities") or {}).get("sessionCapabilities") or {}
        for method in ("session/delete", "session/close"):
            if session and method.split("/")[1] in offered:
                lines.deadline = min(lines.deadline, time.monotonic() + 10)
                try:
                    _rpc(lines, 3, method, {"sessionId": session}, reply_to_requests=True)
                except SwarmError:
                    continue
                break
    return acp_catalog(provider, result)


READERS = {"codex": _codex, "cursor": _cursor, "claude-code": _claude}


def catalog(config, provider) -> dict:
    """{"provider", "models": [{"id", "label", "efforts", "default_effort"?, "default", "description"?, "variants"?}],
    "efforts": [...], "effort_labels"?: {id: native name}, "source": "native" | "none"}. Without a catalog, free text."""
    provider = canonical(provider)
    reader = READERS.get(provider)
    if reader:
        value = reader(command_for(provider, config))
    elif provider in ACP_PROVIDERS or provider == "opencode":
        value = _acp(provider, config)
    else:
        value = None
    if value is None:
        return {"provider": provider, "models": [], "efforts": [], "source": "none"}
    return {"provider": provider, **value, "source": "native"}
