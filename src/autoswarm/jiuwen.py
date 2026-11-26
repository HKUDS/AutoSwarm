"""JiuwenSwarm through its machine protocol, by a bridge run on JiuwenSwarm's own Python."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .nanobot import native_python
from .records import SwarmError


def bridge_command(command, config):
    return [*native_python(command, config, "jiuwen", "JiuwenSwarm"), "-u", str(Path(__file__).with_name("jiuwen_bridge.py")),
            *("--process=" + part for part in command)]


def data_directory() -> Path:
    """JIUWENSWARM_DATA_DIR, else ~/.jiuwenswarm (JiuwenSwarm's own default)."""
    configured = os.environ.get("JIUWENSWARM_DATA_DIR", "").strip()
    return Path(os.path.expandvars(configured)).expanduser() if configured else Path.home() / ".jiuwenswarm"


def models(config):
    """JiuwenSwarm's configured models, as its own model.list query reports them."""
    from .providers import command_for
    query = {"schema_version": "0.1", "type": "query", "request_id": "autoswarm-models", "operation": "model.list", "timeout_seconds": 60}
    result = subprocess.run([*command_for("jiuwen", config), "--query-json", "-"], input=json.dumps(query), capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=180)
    lines = result.stdout.strip().splitlines()
    value = json.loads(lines[-1]) if lines else {}
    if value.get("status") != "completed":
        raise SwarmError("native_config_error", "JiuwenSwarm could not list its models: " + json.dumps(value.get("error") or result.stderr.strip()[-300:], ensure_ascii=False))
    data = value.get("data") or {}
    current = data.get("current_selection")
    return {"models": [{"id": m.get("selection_key") or m["model_name"], "label": m.get("display_name") or m["model_name"], "efforts": [],
                        "default": m.get("selection_key") == current if current else bool(m.get("is_default"))}
                       for m in data.get("models") or [] if isinstance(m, dict) and m.get("model_name")], "efforts": []}
