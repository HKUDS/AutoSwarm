"""Native session selection helpers; catalogs and allowed values stay native."""
from contextlib import contextmanager
from pathlib import Path
import re
import json
import shutil
import tempfile

from .records import SwarmError
from .platform import link_directory, unlink_directory


def choices(options):
    """ACP select options can contain groups as well as plain values."""
    for item in options or []:
        if "value" in item:
            yield item["value"]
        else:
            yield from choices(item.get("options"))


@contextmanager
def native_environment(task, env):
    options = task.get("native_options", {})
    if task["provider"] != "cursor" or not (options.get("model") or options.get("effort")):
        yield env
        return
    # Cursor's ACP setters persist user preferences. Its supported config-dir
    # override keeps these writes process-local while data/session paths stay
    # native. Seed existing root JSON settings (including native auth/MCP),
    # never transcripts/plugins, and delete the private copy on runner exit.
    source = Path(env.get("CURSOR_CONFIG_DIR") or (str(Path(env["XDG_CONFIG_HOME"]) / "cursor") if env.get("XDG_CONFIG_HOME") else Path.home() / ".cursor"))
    with tempfile.TemporaryDirectory(prefix="autoswarm-cursor-") as temporary:
        links = []
        try:
            (source / "acp-sessions").mkdir(parents=True, exist_ok=True)
            for path in source.iterdir():
                target = Path(temporary) / path.name
                if path.is_dir():
                    link_directory(target, path)
                    links.append(target)
                elif path.is_file() and path.suffix == ".json":
                    shutil.copyfile(path, target)
            yield {**env, "CURSOR_CONFIG_DIR": temporary}
        finally:
            for link in reversed(links):
                unlink_directory(link)


def cursor_parameters(model):
    match = re.fullmatch(r"([^\[\]]+)(?:\[([^\[\]]*)\])?", model)
    if not match:
        raise SwarmError("invalid_model", "Cursor expects a native model ID with optional [parameter=value,...]")
    params = {}
    for part in (match[2] or "").split(","):
        if not part:
            continue
        key, separator, value = part.partition("=")
        if not key or not separator or not value:
            raise SwarmError("invalid_model", "Cursor model parameters must use parameter=value")
        params[key] = value
    return match[1], params


def dsh_model_value(options, requested):
    """Resolve only against native DSH routes; selector values are opaque JSON."""
    values = list(choices(options))
    if requested in values:
        return requested
    matches = []
    for value in values:
        try:
            route = json.loads(value)
        except (ValueError, TypeError):
            continue
        if isinstance(route, list) and len(route) == 2 and all(isinstance(v, str) for v in route):
            if requested in {route[0] + "/" + route[1], route[1]}:
                matches.append(value)
    if len(matches) != 1:
        raise SwarmError("invalid_model", "DSH model must select one advertised provider/model route or its exact ACP option value; inspect the native model catalog")
    return matches[0]


def configure_acp(native, result, done):
    """Apply overrides before prompting, including when loading old sessions."""
    provider = native.task["provider"]
    options = native.task.get("native_options", {})
    if provider == "grok":
        # Grok exposes process-scoped native flags. Apply them once at launch,
        # without a second setter that may change an existing user preference.
        done()
        return
    model, effort = options.get("model"), options.get("effort")
    models = result.get("models") or {}
    configs = result.get("configOptions") or []
    params = {}
    if provider == "cursor" and model:
        model, params = cursor_parameters(model)
    effort_applied = False

    def set_parameters(value):
        nonlocal configs, effort_applied
        configs = value.get("configOptions", configs)
        if params:
            config_id = next(iter(params))
            selected = params.pop(config_id)
            control = next((item for item in configs if item.get("id") == config_id), None)
        elif effort and not effort_applied:
            effort_applied = True
            selected = effort
            control = next((item for item in configs if item.get("id") in {"thought_level", "reasoning_effort", "reasoningEffort", "effort", "reasoning"}), None)
            control = control or next((item for item in configs if item.get("category") in {"thought_level", "reasoning_effort"}), None)
        else:
            done()
            return
        if not control and provider in {"copilot", "nanobot"} and not params:
            # Copilot also supports startup --reasoning-effort, including
            # BYOK servers that advertise no session-level model controls.
            done()
            return
        if not control:
            raise SwarmError("unsupported_effort", f"{provider} did not advertise a reasoning-effort control for this model; omit --effort or select a supported model")
        allowed = list(choices(control.get("options")))
        if allowed and selected not in allowed:
            raise SwarmError("unsupported_effort", f"{provider} does not offer {control['id']}={selected!r} for this model; native choices: {', '.join(map(str, allowed))}")
        native.request("session/set_config_option", {"sessionId": native.output.session_id, "configId": control["id"], "value": selected}, set_parameters)
    if model and (models or provider not in {"copilot", "nanobot"}):
        available = models.get("availableModels", [])
        match = next((item for item in available if item.get("modelId") == model), None)
        match = match or next((item for item in available if item.get("name") == model), None)
        if match:
            model = match["modelId"]
        if any(item.get("id") == "model" for item in configs):
            if provider == "dsh":
                model = dsh_model_value(next(item for item in configs if item.get("id") == "model").get("options"), model)
            native.request("session/set_config_option", {"sessionId": native.output.session_id, "configId": "model", "value": model}, set_parameters)
        else:
            native.request("session/set_model", {"sessionId": native.output.session_id, "modelId": model}, set_parameters)
    else:
        set_parameters({})


def validate_opencode_effort(native):
    options = native.task.get("native_options", {})
    effort = options.get("effort")
    if not effort:
        return
    model = options.get("model")
    if not model:
        config = native.http("GET", "/config")
        agent_name = options.get("agent") or config.get("default_agent")
        agents = native.http("GET", "/agent")
        agent = next((item for item in agents if item.get("name") == agent_name), {}) if agent_name else next((item for item in agents if item.get("mode") == "primary" and not item.get("hidden")), {})
        selected = agent.get("model")
        if not selected and native.task.get("native_session_id"):
            history = native.http("GET", "/session/" + native.output.session_id + "/message")
            selected = next((item["info"].get("model") for item in reversed(history) if item.get("info", {}).get("role") == "user"), None)
        model = (selected["providerID"] + "/" + selected["modelID"]) if selected else config.get("model")
    if not model or "/" not in model:
        raise SwarmError("unsupported_effort", "OpenCode did not expose an unambiguous default model for variant validation; select its native provider/model with --model")
    provider, model_id = model.split("/", 1)
    catalog = native.http("GET", "/provider")
    entry = next((item for item in catalog.get("all", []) if item.get("id") == provider), {})
    variants = entry.get("models", {}).get(model_id, {}).get("variants", {})
    if effort not in variants:
        raise SwarmError("unsupported_effort", f"OpenCode model {model} does not advertise variant {effort!r}; native variants: {', '.join(variants) or '(none)'}. Select a supported variant or omit --effort")
