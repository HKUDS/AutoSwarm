"""Install a relocatable Skill and its host return integration."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .providers import discover
from .records import Records, read_json, write_json


def install(records: Records, host: str, skill_dir: str | None = None, settings_file: str | None = None, plugin_dir: str | None = None) -> dict:
    defaults = {
        "codex": Path.home() / ".agents/skills/autoswarm",
        "claude-code": Path.home() / ".claude/skills/autoswarm",
        "opencode": Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode/skills/autoswarm",
        "cursor": Path.home() / ".cursor/skills/autoswarm",
        "copilot": Path.home() / ".copilot/skills/autoswarm",
        "muse": Path.home() / ".agents/skills/autoswarm",
        "codebuddy": Path.home() / ".codebuddy/skills/autoswarm",
    }
    destination = Path(skill_dir).expanduser().resolve() if skill_dir else defaults[host]
    assets = Path(__file__).parent / "assets"
    destination.mkdir(parents=True, exist_ok=True)
    scripts = destination / "scripts"
    scripts.mkdir(exist_ok=True)
    launcher = scripts / "launch.py"
    package = scripts / "runtime/autoswarm"
    source = Path(__file__).parent
    if source.resolve() != package.resolve():
        if source.resolve() in package.resolve().parents:
            raise ValueError("Install the skill outside the AutoSwarm package directory")
        shutil.copytree(source, package, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "SKILL.md"))
    # Remove only the obsolete template owned by earlier AutoSwarm installs.
    (package / "assets/SKILL.md").unlink(missing_ok=True)
    launcher.write_text("import os, sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parent / 'runtime'))\nos.environ.setdefault('AUTOSWARM_HOME', " + repr(str(records.home)) + ")\nfrom autoswarm.cli import main\nmain()\n", encoding="utf-8")
    argv = [sys.executable, str(launcher), "--home", str(records.home)]
    skill = (assets / "skill-template.md").read_text(encoding="utf-8")
    shutil.copytree(assets / "references", destination / "references", dirs_exist_ok=True)
    (destination / "SKILL.md").write_text(skill + "\nInstalled launcher argv (append CLI arguments; preserve each argv entry):\n\n```json\n" + json.dumps(argv, ensure_ascii=False) + "\n```\n", encoding="utf-8")
    integration = {"host": host, "skill": str(destination), "launcher": argv, "native_return": "endpoint_required"}
    if host == "claude-code":
        path = Path(settings_file).expanduser() if settings_file else Path.home() / ".claude/settings.json"
        settings = read_json(path, {})
        hooks = settings.setdefault("hooks", {})
        for event, action in (("SessionStart", "session-start"), ("PostToolUse", "post-tool"), ("Stop", "stop")):
            command = {"type": "command", "command": argv[0], "args": [*argv[1:], "hook", "--host", host, "--event", action]}
            if event == "PostToolUse":
                command["asyncRewake"] = True
            entries = hooks.setdefault(event, [])
            # Replace only the integration installed at this same launcher.
            kept = []
            for entry in entries:
                remaining = [hook for hook in entry.get("hooks", []) if not (str(launcher) in hook.get("args", []) or str(launcher) == hook.get("command"))]
                if remaining:
                    kept.append({**entry, "hooks": remaining})
            entries[:] = kept
            entries.append({"hooks": [command]})
        write_json(path, settings)
        integration.update({"settings": str(path), "native_return": "async_rewake_and_stop_hook", "note": "Start a new native session so SessionStart binds its identity. Native hook trust and lifecycle limits remain in force."})
    elif host == "opencode":
        directory = Path(plugin_dir).expanduser() if plugin_dir else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode/plugins"
        directory.mkdir(parents=True, exist_ok=True)
        plugin = directory / "autoswarm.js"
        plugin.write_text((assets / "opencode.js").read_text(encoding="utf-8").replace("__AUTOSWARM_COMMAND__", json.dumps(argv)), encoding="utf-8")
        integration.update({"plugin": str(plugin), "native_return": "native_plugin", "note": "Restart OpenCode and use its autoswarm tool for automatically bound callbacks."})
    elif host == "codex":
        integration["note"] = "CODEX_THREAD_ID binds the current session automatically. Native callbacks also require its existing app-server endpoint; AUTOSWARM_CODEX_ENDPOINT or an explicit parent binding supplies it. Otherwise use wait."
    else:
        integration.update({"native_return": "wait", "note": "Use run/background and wait in the current turn to collect results and approvals. No original-session wake-up integration is installed for this host. Reload native skills if needed."})
    integration["agents"] = discover(records.config())
    return integration
