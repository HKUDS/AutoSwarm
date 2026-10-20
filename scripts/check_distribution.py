"""Check the installed package and Skill outside the source directory."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", nargs=argparse.REMAINDER, help="-- /absolute/path/to/autoswarm")
    args = parser.parse_args()
    program = args.program or ["autoswarm"]
    if program[0] == "--":
        program.pop(0)
    program[0] = str(Path(shutil.which(program[0]) or program[0]).resolve())
    fixture = Path(__file__).resolve().parent.parent / "tests/fixtures/provider.py"
    with tempfile.TemporaryDirectory(prefix="autoswarm-distribution-") as temporary:
        root = Path(temporary)
        home = root / "state"
        home.mkdir()
        (home / "config.json").write_text(json.dumps({"providers": {name: {"command": [sys.executable, str(fixture), name]} for name in ("codex", "claude-code", "opencode")}}), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH="", AUTOSWARM_HOME=str(home))
        env.pop("AUTOSWARM_PARENT", None)
        env.pop("CODEX_THREAD_ID", None)
        def invoke(argv, command=program):
            process = subprocess.run([*command, *argv], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8")
            assert process.returncode == 0, {"argv": argv, "exit_code": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
            value = json.loads(process.stdout)
            assert value["ok"], value
            return value["data"]
        for provider in ("codex", "claude-code", "opencode"):
            result = invoke(["run", "--agent", provider, "--no-parent", "--cwd", str(root), "安装包测试🐝"])
            assert result["outcome"] == "completed", result
            assert result["content"].startswith("安装包测试🐝"), result
            resumed = invoke(["resume", result["call_id"], "--no-parent", "resume packaged"])
            assert resumed["native_session_id"] == result["native_session_id"], resumed
            assert resumed["outcome"] == "completed", resumed
        assert invoke(["config"])["delegate_approvals"] is True
        for provider in ("codex", "claude-code", "opencode"):
            marker = root / (provider + "-approved")
            call = invoke(["run", "--agent", provider, "--no-parent", "--background", "--cwd", str(root), "approval:" + str(marker)])
            request = invoke(["wait", call["call_id"]])[0]
            assert request.get("kind") == "approval_required" and not marker.exists(), request
            invoke(["approve", call["call_id"], request["request_id"]])
            result = invoke(["wait", call["call_id"]])[0]
            assert result["outcome"] == "completed" and marker.exists(), result
        assert invoke(["config", "--delegate-approvals", "off"])["delegate_approvals"] is False
        assert invoke(["config", "--delegate-approvals", "on"])["delegate_approvals"] is True
        codex = invoke(["setup", "--host", "codex", "--skill-dir", str(root / "codex-skill")])
        # Reinstall from the Skill's own launcher.
        invoke(["setup", "--host", "codex", "--skill-dir", str(root / "codex-skill")], command=codex["launcher"])
        result = invoke(["run", "--agent", "codex", "--no-parent", "--cwd", str(root), "installed launcher"], command=codex["launcher"])
        assert result["outcome"] == "completed", result
        settings = root / "claude/settings.json"
        invoke(["setup", "--host", "claude-code", "--skill-dir", str(root / "claude-skill"), "--settings-file", str(settings)])
        assert json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PostToolUse"][0]["hooks"][0]["asyncRewake"]
        plugin = invoke(["setup", "--host", "opencode", "--skill-dir", str(root / "opencode-skill"), "--plugin-dir", str(root / "plugins")])
        assert "__AUTOSWARM_COMMAND__" not in Path(plugin["plugin"]).read_text(encoding="utf-8")
    print("Distribution passed: three native transports, approval round trips, delegation config, native resume, installed launchers, reinstall and bundled host integrations")


if __name__ == "__main__":
    main()
