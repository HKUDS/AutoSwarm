"""Whether each main agent can find a working AutoSwarm Skill (read only)."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .providers import BINARIES
from .records import Records, SwarmError

HOSTS = tuple(BINARIES)  # Every agent AutoSwarm launches can be a main agent with the Skill.
LAUNCHER = re.compile(r"Installed launcher argv[^\n]*\n+```json\s*(\[.*?\])\s*```", re.S)


def hermes_home() -> Path:
    """HERMES_HOME, else Hermes's platform default (%LOCALAPPDATA%\\hermes on Windows)."""
    if os.environ.get("HERMES_HOME", "").strip():
        return Path(os.path.expandvars(os.environ["HERMES_HOME"])).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "hermes"
    return Path.home() / ".hermes"


def roots(host: str, config: dict | None = None) -> list[Path]:
    """User skill folders the host reads, setup's destination first.

    Besides its own folder, Cursor reads .claude/skills, .codex/skills and
    .agents/skills; Copilot, Grok and Kimi read .agents/skills too (each lists the
    skills installed there among its own commands).
    """
    home = Path.home()
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    shared = home / ".agents/skills"
    if host == "nanobot":
        # Its configured workspace's skills folder, as its own loader resolves it.
        from .nanobot import skill_directory
        try:
            return [skill_directory(config or {}).parent]
        except (SwarmError, OSError):
            return []
    return {"claude-code": [home / ".claude/skills"],
            "codex": [shared, codex / "skills"],
            "cursor": [home / ".cursor/skills", shared, home / ".claude/skills", codex / "skills"],
            "opencode": [Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "opencode/skills"],
            "copilot": [home / ".copilot/skills", shared],
            "muse": [shared],
            "codebuddy": [home / ".codebuddy/skills"],
            "hermes": [hermes_home() / "skills"],
            "dsh": [Path(os.environ["DSH_AGENTS_HOME"]) / "skills" if os.environ.get("DSH_AGENTS_HOME") else shared],
            "grok": [home / ".grok/skills", shared],
            "kimi": [Path(os.environ.get("KIMI_CODE_HOME") or home / ".kimi-code") / "skills", shared]}[host]


def destination(host: str, config: dict | None = None) -> Path:
    """Where setup installs the host's Skill."""
    found = roots(host, config)
    if not found:
        raise SwarmError("native_config_error", f"Cannot resolve where {host} reads its skills; use setup --skill-dir")
    return found[0] / "autoswarm"


# How a conversation's first turn names the Skill, as the user would type it in that agent.
# Codex takes a skill input item; agents not listed here are told in words where the Skill is.
MENTIONS = {"claude-code": "/autoswarm", "cursor": "/autoswarm", "copilot": "/autoswarm", "grok": "/autoswarm",
            "codebuddy": "/autoswarm", "kimi": "/skill:autoswarm"}


def host_status(host: str, *, verify: bool = False, config: dict | None = None) -> dict:
    found = next((root / "autoswarm" for root in roots(host, config) if (root / "autoswarm/SKILL.md").is_file()), None)
    if not found:
        return {"state": "missing"}
    value, problems = {"path": str(found)}, []
    try:
        match = LAUNCHER.search((found / "SKILL.md").read_text(encoding="utf-8"))
        argv = json.loads(match.group(1)) if match else None
    except (OSError, ValueError):
        argv = None
    if isinstance(argv, list) and "--home" in argv[:-1]:
        value["home"] = argv[argv.index("--home") + 1]  # The record directory this Skill's agent writes to.
    if not (isinstance(argv, list) and len(argv) >= 2 and all(isinstance(x, str) for x in argv)):
        problems.append("SKILL.md has no launcher argv")
    elif not Path(argv[0]).is_file() or not Path(argv[1]).is_file():
        problems.append("missing " + (argv[0] if not Path(argv[0]).is_file() else argv[1]))
    elif verify:
        try:
            run = subprocess.run([*argv, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if run.returncode or "AutoSwarm" not in run.stdout:
                problems.append("launcher failed: " + (run.stderr or run.stdout).strip()[-1500:])
        except (OSError, subprocess.SubprocessError) as error:
            problems.append("launcher failed: " + str(error))
    if not problems and outdated(found):
        value["outdated"] = True
    return {**value, "state": "broken" if problems else "ready", **({"problems": problems} if problems else {})}


SOURCE = Path(__file__).parent
_VERSION = re.compile(r"__version__\s*=\s*['\"]([^'\"]+)")


def _version(package: Path) -> tuple:
    try:
        found = _VERSION.search((package / "__init__.py").read_text(encoding="utf-8"))
    except OSError:
        return ()
    return tuple(int(part) for part in re.findall(r"\d+", found.group(1))) if found else ()


def outdated(skill: Path) -> bool:
    """The skill's own copy of AutoSwarm differs from this one, and is not newer."""
    copy = skill / "scripts/runtime/autoswarm"
    if not copy.is_dir() or copy.resolve() == SOURCE.resolve() or _version(copy) > _version(SOURCE):
        return False
    for file in SOURCE.rglob("*.py"):
        twin = copy / file.relative_to(SOURCE)
        try:
            if twin.read_bytes() != file.read_bytes():
                return True
        except OSError:
            return True
    return False


def same_home(first, second) -> bool:
    try:
        return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))
    except OSError:
        return False


def refresh(records: Records, host: str) -> dict:
    """Bring an installed skill's AutoSwarm copy, references and text up to date, keeping its launcher argv."""
    import shutil
    found = host_status(host, config=records.config())
    if found.get("state") != "ready" or not found.get("outdated"):
        return found
    if found.get("home") and not same_home(found["home"], records.home):
        return found  # It belongs to another record directory; that one's AutoSwarm keeps it.
    skill = Path(found["path"])
    argv = LAUNCHER.search((skill / "SKILL.md").read_text(encoding="utf-8")).group(1)
    shutil.copytree(SOURCE, skill / "scripts/runtime/autoswarm", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "SKILL.md"))
    shutil.copytree(SOURCE / "assets/references", skill / "references", dirs_exist_ok=True)
    template = (SOURCE / "assets/skill-template.md").read_text(encoding="utf-8")
    (skill / "SKILL.md").write_text(template + "\nInstalled launcher argv (append CLI arguments; preserve each argv entry):\n\n```json\n" + argv + "\n```\n", encoding="utf-8")
    return host_status(host, config=records.config())


def status(records: Records, hosts: list[str] | None = None, *, verify: bool = False) -> dict:
    from .providers import canonical
    try:
        hosts = [canonical(host) for host in hosts or []]
    except SwarmError:
        hosts = [None]
    if any(host not in HOSTS for host in hosts):
        raise SwarmError("invalid_input", "Skill status covers " + ", ".join(HOSTS))
    config = records.config()
    return {"home": str(records.home), "hosts": {host: host_status(host, verify=verify, config=config) for host in hosts or HOSTS}}
