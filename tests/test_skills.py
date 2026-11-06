"""Desk's AutoSwarm switch: which hosts find a working Skill, and hiding it from one conversation."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import engine, platform, skills
from autoswarm.desk import control
from autoswarm.records import Records, SwarmError, read_json, write_json
from autoswarm.setup import install
if __package__:
    from .autoswarm_test_support import cleanup_temporary
else:
    from autoswarm_test_support import cleanup_temporary

FIXTURE = Path(__file__).parent / "fixtures/provider.py"
CLEAN = {key: value for key, value in os.environ.items()
         if not key.startswith("AUTOSWARM_") and key not in {"CODEX_THREAD_ID", "CODEX_HOME"}}


class SkillStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skills-中文 ")
        self.user = Path(self.temp.name).resolve() / "user home"
        self.user.mkdir()
        self.records = Records(Path(self.temp.name) / "records")
        # Nanobot's skills folder is asked of its own loader; this one has no nanobot.
        write_json(self.records.home / "config.json", {"providers": {"nanobot": {"command": [str(Path(self.temp.name) / "no-nanobot.exe")]}}})
        self.patches = [patch.object(Path, "home", return_value=self.user),
                        patch.dict(os.environ, {**{key: value for key, value in CLEAN.items() if key not in {"HERMES_HOME", "KIMI_CODE_HOME", "DSH_AGENTS_HOME", "XDG_CONFIG_HOME"}},
                                                "LOCALAPPDATA": str(Path(self.temp.name) / "local app data")}, clear=True)]
        for value in self.patches:
            value.start()

    def tearDown(self):
        for value in reversed(self.patches):
            value.stop()
        self.temp.cleanup()

    def test_missing_ready_shared_and_broken(self):
        self.assertEqual({h: v["state"] for h, v in skills.status(self.records)["hosts"].items()},
                         {host: "missing" for host in ("claude-code", "codex", "cursor", "opencode", "copilot", "muse", "codebuddy", "hermes", "nanobot", "dsh", "grok", "kimi")})
        install(self.records, "codex", discover_agents=False)
        value = skills.status(self.records, verify=True)["hosts"]
        self.assertEqual(value["codex"], {"path": str(self.user / ".agents/skills/autoswarm"), "home": str(self.records.home), "state": "ready"})
        # Cursor, Copilot, Grok, Kimi, DSH and Muse read ~/.agents/skills too, so the Codex copy serves them.
        for host in ("cursor", "copilot", "grok", "kimi", "dsh", "muse"):
            self.assertEqual(value[host]["path"], str(self.user / ".agents/skills/autoswarm"), host)
        self.assertEqual({h: value[h]["state"] for h in ("claude-code", "opencode", "codebuddy", "hermes", "nanobot")},
                         dict.fromkeys(("claude-code", "opencode", "codebuddy", "hermes", "nanobot"), "missing"))
        (self.user / ".agents/skills/autoswarm/scripts/launch.py").unlink()
        value = skills.status(self.records, ["codex"])["hosts"]["codex"]
        self.assertEqual(value["state"], "broken")
        self.assertIn("launch.py", value["problems"][0])
        (self.user / ".agents/skills/autoswarm/SKILL.md").write_text("---\nname: autoswarm\n---\nhand written\n", encoding="utf-8")
        self.assertEqual(skills.status(self.records, ["codex"])["hosts"]["codex"]["problems"], ["SKILL.md has no launcher argv"])
        with self.assertRaises(SwarmError):
            skills.status(self.records, ["not-an-agent"])
        self.assertIn("kimi", skills.status(self.records, ["kimi-code"])["hosts"], "aliases name the agent")

    def test_each_agent_installs_where_it_reads_skills(self):
        local = Path(self.temp.name) / "local app data"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
            expected = {"opencode": self.user / ".config/opencode/skills/autoswarm", "codebuddy": self.user / ".codebuddy/skills/autoswarm",
                        "grok": self.user / ".grok/skills/autoswarm", "kimi": self.user / ".kimi-code/skills/autoswarm",
                        "hermes": (local / "hermes" if os.name == "nt" else self.user / ".hermes") / "skills/autoswarm"}
            for host, path in expected.items():
                self.assertEqual(skills.destination(host), path, host)
                self.assertEqual(Path(install(self.records, host, discover_agents=False)["skill"]), path.resolve(), host)
                self.assertEqual(skills.host_status(host)["state"], "ready", host)
            with patch.dict(os.environ, {"HERMES_HOME": str(self.user / "custom hermes")}):
                self.assertEqual(skills.destination("hermes"), self.user / "custom hermes/skills/autoswarm")

    def test_cli_reports_status(self):
        env = dict(CLEAN, USERPROFILE=str(self.user), HOME=str(self.user), PYTHONPATH=str(Path(__file__).resolve().parent.parent / "src"))
        run = lambda *args: json.loads(subprocess.run([sys.executable, "-m", "autoswarm", "--home", str(self.records.home), *args], env=env,
                                                      capture_output=True, text=True, encoding="utf-8", timeout=60).stdout)
        self.assertTrue(run("setup", "--host", "claude-code")["ok"])
        self.assertEqual(run("skills", "--host", "claude-code", "--verify")["data"]["hosts"]["claude-code"]["state"], "ready")


    def test_an_outdated_copy_is_refreshed_and_keeps_its_launcher(self):
        install(self.records, "claude-code")
        skill = self.user / ".claude/skills/autoswarm"
        launcher = skills.LAUNCHER.search((skill / "SKILL.md").read_text(encoding="utf-8")).group(1)
        self.assertNotIn("outdated", skills.host_status("claude-code"))
        engine_copy = skill / "scripts/runtime/autoswarm/engine.py"
        engine_copy.write_text("# an older AutoSwarm", encoding="utf-8")
        (skill / "SKILL.md").write_text((skill / "SKILL.md").read_text(encoding="utf-8").replace("Coordinate", "Old text"), encoding="utf-8")
        self.assertTrue(skills.host_status("claude-code")["outdated"])
        refreshed = skills.refresh(self.records, "claude-code")
        self.assertEqual(refreshed["state"], "ready")
        self.assertNotIn("outdated", refreshed)
        self.assertEqual(engine_copy.read_bytes(), (Path(skills.__file__).parent / "engine.py").read_bytes())
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("Old text", text)
        self.assertEqual(skills.LAUNCHER.search(text).group(1), launcher, "The installed launcher stays as it was")
        # A copy whose launcher writes to another record directory is left to that directory's AutoSwarm.
        engine_copy.write_text("# an older AutoSwarm", encoding="utf-8")
        other = Records(Path(self.temp.name) / "other records")
        self.assertEqual(skills.refresh(other, "claude-code")["home"], str(self.records.home))
        self.assertEqual(engine_copy.read_text(encoding="utf-8"), "# an older AutoSwarm")
        # A newer copy (installed by a newer AutoSwarm) is never replaced by this one.
        engine_copy.write_text("# a newer AutoSwarm", encoding="utf-8")
        init = skill / "scripts/runtime/autoswarm/__init__.py"
        init.write_text('__version__ = "99.0.0"', encoding="utf-8")
        self.assertNotIn("outdated", skills.host_status("claude-code"))
        skills.refresh(self.records, "claude-code")
        self.assertEqual(engine_copy.read_text(encoding="utf-8"), "# a newer AutoSwarm")


class SkillSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skill-switch-中文-")
        self.root = Path(self.temp.name).resolve()
        self.records = Records(self.root / "records")
        write_json(self.records.home / "config.json", {"providers": {
            provider: {"command": [sys.executable, str(FIXTURE), provider]} for provider in ["claude-code", "codex", "cursor", "kimi", "hermes"]}})
        self.patches = [patch.dict(os.environ, dict(CLEAN, PYTHONPATH=str(Path(__file__).resolve().parent.parent / "src"), HERMES_HOME=str(self.root / "hermes")), clear=True),
                        patch.object(Path, "home", return_value=self.root / "user")]
        for value in self.patches:
            value.start()
        self.controller = control.initialize(self.records)["controller_id"]

    def tearDown(self):
        calls = [task["call_id"] for _, task in self.records.calls()]
        for cid in calls:
            engine.cancel(self.records, cid)
        deadline = time.monotonic() + 10
        while any(platform.running(read_json(self.records.call(cid) / "handle.json")) for cid in calls) and time.monotonic() < deadline:
            time.sleep(.01)
        for value in reversed(self.patches):
            value.stop()
        cleanup_temporary(self.temp, deadline)

    def start(self, provider, skill):
        call = control.start(self.records, self.controller, "work", provider=provider, workspace=str(self.root), skill=skill)
        return read_json(self.records.call(call["call_id"]) / "task.json"), engine.wait(self.records, [call["call_id"]])[0]

    def test_on_mentions_the_skill_natively_in_the_first_turn_only(self):
        with self.assertRaises(SwarmError) as raised:
            self.start("codex", True)
        self.assertEqual(raised.exception.code, "skill_not_installed")
        install(self.records, "claude-code")
        install(self.records, "codex")
        for provider, typed in (("claude-code", "/autoswarm "), ("cursor", "/autoswarm "), ("codex", "$autoswarm ")):
            task, result = self.start(provider, True)
            self.assertEqual(task["skill"]["name"], "autoswarm")
            self.assertTrue(Path(task["skill"]["path"]).is_file())
            self.assertEqual(task["provider_args"], [])
            self.assertTrue(result["content"].startswith(typed + "work"), (provider, result["content"][:40]))
            later = engine.wait(self.records, [engine.resume(self.records, task["call_id"], "continue")["call_id"]])[0]
            self.assertTrue(later["content"].startswith("continue"), "Later turns are not mentioned again")
        task, result = self.start("codex", None)
        self.assertNotIn("skill", task)
        self.assertTrue(result["content"].startswith("work"))

    def test_every_agent_can_start_with_autoswarm(self):
        install(self.records, "codex", discover_agents=False)  # ~/.agents/skills: Kimi reads it too.
        task, result = self.start("kimi", True)
        self.assertTrue(result["content"].startswith("/skill:autoswarm work"), result["content"][:60])
        with self.assertRaises(SwarmError) as raised:
            self.start("hermes", True)
        self.assertEqual(raised.exception.code, "skill_not_installed")
        install(self.records, "hermes", discover_agents=False)
        task, result = self.start("hermes", True)
        self.assertEqual(Path(task["skill"]["path"]), self.root / "hermes/skills/autoswarm/SKILL.md")
        # Hermes lists no skills among its commands; it is told where the Skill is.
        self.assertTrue(result["content"].startswith("Use your autoswarm skill for this task; its instructions are in " + task["skill"]["path"]), result["content"][:120])
        self.assertTrue(result["content"].split("\n\n", 1)[1].startswith("work"))
        with self.assertRaises(SwarmError) as raised:
            control.start(self.records, self.controller, "x", provider="kimi", workspace=str(self.root), skill=False)
        self.assertEqual(raised.exception.code, "unsupported_native_option", "Only Claude Code and Codex hide a Skill for one conversation")

    def test_off_hides_the_skill_in_this_and_later_turns(self):
        for provider, expected in (("claude-code", ["--settings", '{"skillOverrides": {"autoswarm": "off"}}']),
                                   ("codex", ["-c", 'skills.config=[{name="autoswarm",enabled=false}]'])):
            task, _ = self.start(provider, False)
            self.assertEqual(task["provider_args"], expected)
            self.assertEqual(task["command"][-len(expected):], expected)
            self.assertNotIn("skill", task)
            later = engine.resume(self.records, task["call_id"], "continue")
            self.assertEqual(read_json(self.records.call(later["call_id"]) / "task.json")["provider_args"], expected)
        with self.assertRaises(SwarmError) as raised:
            control.start(self.records, self.controller, "x", provider="cursor", workspace=str(self.root), skill=False)
        self.assertEqual(raised.exception.code, "unsupported_native_option")
        self.assertNotIn(" run ", control.SESSION_RULES, "Delegation guidance comes from the Skill, not Desk")


if __name__ == "__main__":
    unittest.main()
