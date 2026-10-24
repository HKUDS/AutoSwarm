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


PROVIDERS = ("codex", "claude-code", "opencode", "cursor", "copilot", "muse", "codebuddy", "hermes", "nanobot", "dsh", "grok")

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
        (home / "config.json").write_text(json.dumps({"providers": {name: {"command": [sys.executable, str(fixture), name], **({"python": [sys.executable, str(fixture), name]} if name == "nanobot" else {})} for name in PROVIDERS}}), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH="", AUTOSWARM_HOME=str(home), CURSOR_CONFIG_DIR=str(root / "native-cursor"))
        env.pop("AUTOSWARM_PARENT", None)
        env.pop("CODEX_THREAD_ID", None)
        def invoke(argv, command=program):
            process = subprocess.run([*command, *argv], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8")
            assert process.returncode == 0, {"argv": argv, "exit_code": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
            value = json.loads(process.stdout)
            assert value["ok"], value
            return value["data"]
        for provider in PROVIDERS:
            result = invoke(["run", "--agent", provider, "--no-parent", "--cwd", str(root), "安装包测试🐝"])
            assert result["outcome"] == "completed", result
            assert result["content"].startswith("安装包测试🐝"), result
            resumed = invoke(["resume", result["call_id"], "--no-parent", "resume packaged"])
            assert resumed["native_session_id"] == result["native_session_id"], resumed
            assert resumed["outcome"] == "completed", resumed
            model = "fixture/native-model" if provider == "opencode" else "native-model"
            selected = invoke(["run", "--agent", provider, "--no-parent", "--cwd", str(root), "--model", model, "--effort", "deep-native", "--provider-arg=selection-catalog", "selected package call"])
            assert selected["outcome"] == "completed", selected
            continued = invoke(["resume", selected["call_id"], "--no-parent", "selected package resume"])
            assert continued["outcome"] == "completed" and continued["requested_selection"] == {"model": model, "effort": "deep-native"}, continued
        assert invoke(["config"])["delegate_approvals"] is True
        for provider in PROVIDERS:
            marker = root / (provider + "-approved")
            call = invoke(["run", "--agent", provider, "--no-parent", "--background", "--cwd", str(root), "approval:" + str(marker)])
            assert call["return_mode"] == "wait", call
            request = invoke(["wait", call["call_id"]])[0]
            assert request.get("kind") == "approval_required" and not marker.exists(), request
            invoke(["approve", call["call_id"], request["request_id"]])
            result = invoke(["wait", call["call_id"]])[0]
            assert result["outcome"] == "completed" and marker.exists(), result
        assert invoke(["config", "--delegate-approvals", "off"])["delegate_approvals"] is False
        assert invoke(["config", "--delegate-approvals", "on"])["delegate_approvals"] is True
        codex = invoke(["setup", "--host", "codex", "--skill-dir", str(root / "codex-skill")])
        assert list((root / "codex-skill").rglob("SKILL.md")) == [root / "codex-skill/SKILL.md"]
        assert (root / "codex-skill/references/setup.md").is_file()
        assert (root / "codex-skill/references/models.md").is_file()
        assert (root / "codex-skill/references/interactions.md").is_file()
        assert (root / "codex-skill/references/agent-capabilities.md").is_file()
        # Reinstall from the Skill's own launcher.
        invoke(["setup", "--host", "codex", "--skill-dir", str(root / "codex-skill")], command=codex["launcher"])
        result = invoke(["run", "--agent", "codex", "--no-parent", "--cwd", str(root), "--model", "native-model", "--effort", "native-effort", "installed launcher"], command=codex["launcher"])
        assert result["outcome"] == "completed", result
        assert result["requested_selection"] == {"model": "native-model", "effort": "native-effort"}, result
        checked = invoke(["doctor", "--agent", "opencode", "--probe", "--cwd", str(root)], command=codex["launcher"])
        assert checked["verified"], checked
        # A copied Skill must answer native questions using its own runtime.
        answer_file = root / "answer.json"
        answer_file.write_text(json.dumps({"answers": {"Choose scope?": "本地"}}, ensure_ascii=False), encoding="utf-8")
        marker = root / "native-question-answered"
        call = invoke(["run", "--agent", "claude-code", "--no-parent", "--background", "--cwd", str(root), "question:" + str(marker)], command=codex["launcher"])
        request = invoke(["wait", call["call_id"]], command=codex["launcher"])[0]
        assert request["kind"] == "interaction_required" and not marker.exists(), request
        invoke(["answer", call["call_id"], request["request_id"], "--input", str(answer_file)], command=codex["launcher"])
        result = invoke(["wait", call["call_id"]], command=codex["launcher"])[0]
        assert result["outcome"] == "completed" and marker.exists(), result
        # Exercise the copied runtime's messaging, not the source checkout.
        conversation = fixture.with_name("conversation.py")
        config = json.loads((home / "config.json").read_text(encoding="utf-8"))
        config["providers"]["codex"]["command"] = [sys.executable, str(conversation), "codex"]
        (home / "config.json").write_text(json.dumps(config), encoding="utf-8")
        gate, proof = root / "message.gate", root / "message.proof"
        call = invoke(["run", "--agent", "codex", "--name", "worker", "--no-parent", "--background", "--cwd", str(root), json.dumps({"gate": str(gate), "proof": str(proof)})], command=codex["launcher"])
        controller = ["--controller", call["controller_id"]]
        sent = invoke([*controller, "send", "worker", "--interrupt", "packaged messaging"], command=codex["launcher"])
        replies = invoke([*controller, "wait", "--message", sent["message_id"]], command=codex["launcher"])
        assert replies[0]["content"] == "reply:packaged messaging" and not proof.exists(), replies
        gate.touch()
        result = invoke([*controller, "wait", call["call_id"]], command=codex["launcher"])[0]
        assert result["outcome"] == "completed" and proof.exists(), result
        settings = root / "claude/settings.json"
        invoke(["setup", "--host", "claude-code", "--skill-dir", str(root / "claude-skill"), "--settings-file", str(settings)])
        assert json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PostToolUse"][0]["hooks"][0]["asyncRewake"]
        plugin = invoke(["setup", "--host", "opencode", "--skill-dir", str(root / "opencode-skill"), "--plugin-dir", str(root / "plugins")])
        assert "__AUTOSWARM_COMMAND__" not in Path(plugin["plugin"]).read_text(encoding="utf-8")
    print("Distribution passed: eleven providers, approval and question round trips, native resume, installed-launcher messaging and continuation, reinstall and bundled host integrations")


if __name__ == "__main__":
    main()
