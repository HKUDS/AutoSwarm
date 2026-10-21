"""Resolve the installed nanobot Python runtime without adding dependencies."""
from pathlib import Path
import shlex
import shutil
import json
import subprocess

from .records import SwarmError


def bridge_command(command, config):
    selected = config.get("providers", {}).get("nanobot", {}).get("python")
    if selected is not None:
        if not isinstance(selected, list) or not selected or not all(isinstance(v, str) and v for v in selected):
            raise SwarmError("invalid_config", "providers.nanobot.python must be a native Python argv array")
        binary = shutil.which(selected[0])
        if not binary:
            raise SwarmError("provider_unavailable", "Configured nanobot Python is unavailable")
        runtime = [binary, *selected[1:]]
    else:
        cli = Path(command[0]).resolve()
        runtime = None
        # pip/uv/pipx launchers retain the interpreter in their shebang. Keep
        # the venv spelling: resolving Python's symlink would lose its packages.
        if cli.suffix.lower() != ".exe":
            with cli.open("rb") as stream:
                first = stream.readline(4096).decode("utf-8", errors="replace").strip()
            if first.startswith("#!"):
                argv = shlex.split(first[2:])
                if argv and Path(argv[0]).name == "env":
                    argv = argv[1:]
                    if argv and argv[0] == "-S":
                        argv = argv[1:]
                    if argv and "python" in Path(argv[0]).name.lower() and shutil.which(argv[0]):
                        argv[0] = shutil.which(argv[0])
                if argv and "python" in Path(argv[0]).name.lower() and Path(argv[0]).is_file():
                    runtime = argv
        if runtime is None:
            for candidate in (cli.parent / "python.exe", cli.parent.parent / "python.exe", cli.parent / "python", cli.parent / "python3"):
                if candidate.is_file():
                    runtime = [str(candidate)]
                    break
        if runtime is None:
            raise SwarmError("native_runtime_unavailable", "Cannot locate nanobot's Python from its CLI launcher. Retain the native environment and configure providers.nanobot.python as an argv array; do not install another nanobot or use an unrelated Python.")
    return [*runtime, "-u", str(Path(__file__).with_name("nanobot_bridge.py")), *command[1:]]


def skill_directory(config):
    """Ask the installed native loader where this profile keeps its skills."""
    from .providers import invocation
    result = subprocess.run([*invocation({"provider": "nanobot"}, config), "--skill-directory"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise SwarmError("native_config_error", "Cannot resolve nanobot's native workspace; inspect its native configuration or use setup --skill-dir with the existing workspace's skills directory")
    try:
        directory = json.loads(result.stdout)
        if not isinstance(directory, str) or not directory:
            raise ValueError("Invalid native skill path")
        return Path(directory)
    except (ValueError, TypeError) as error:
        raise SwarmError("native_config_error", "Nanobot returned no usable native skill directory; use setup --skill-dir") from error
