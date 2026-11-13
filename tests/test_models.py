"""Model catalogs the agents report themselves (shapes recorded on this machine:
Codex 0.166 model/list and config/read, Cursor Agent 2026.10 --list-models, Claude Code 2.1
initialize and --help, and the ACP session/new of Grok 1.0, CodeBuddy 2.170, DSH 0.2,
OpenCode 2.0, Copilot 1.0, Kimi 2.5 and Hermes 0.22)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import models

FIXTURE = Path(__file__).parent / "fixtures/provider.py"

CURSOR = """Available models

auto - Auto (current, default)
composer-2.5 - Composer 2.5
grok-4.7-low - Grok 4.7  Low
grok-4.7-low-fast - Grok 4.7  Low Fast​​
grok-4.7-high - Grok 4.7  High
grok-4.7-xhigh - Grok 4.7  Extra High
deepseek-v41-flash-1017-none - DeepSeek V4.1 Flash 1017 None
deepseek-v41-flash-1017-high - DeepSeek V4.1 Flash 1017
"""
CLAUDE = """  --effort <level>                      Effort level for the current session
                                        (low, medium, high, xhigh, max)
  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name.
  --name <name>                         Name
"""
FAKE_CLAUDE = textwrap.dedent('''
    import json, sys
    from pathlib import Path
    Path(__file__).with_name("argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
    models = [
        {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)", "supportsEffort": True, "supportedEffortLevels": ["low", "high", "max"]},
        {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus", "description": "Opus 5.5 · Best for everyday, complex tasks", "supportsEffort": True, "supportedEffortLevels": ["low", "high", "max"]},
        {"value": "haiku", "resolvedModel": "claude-haiku-5-5", "displayName": "Haiku", "supportsEffort": False},
        {"value": "glm-5", "displayName": "glm-5", "supportedEffortLevels": ["high"], "supportsEffort": True},
    ]
    for line in sys.stdin:
        value = json.loads(line)
        if value.get("type") == "control_request" and value["request"]["subtype"] == "initialize":
            print(json.dumps({"type": "system", "subtype": "hook_started"}), flush=True)
            print(json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": value["request_id"],
                              "response": {"models": models, "commands": []}}}), flush=True)
''')
FAKE_CODEX = textwrap.dedent('''
    import json, os, sys
    for line in sys.stdin:
        value = json.loads(line)
        if value.get("method") == "initialize":
            print(json.dumps({"id": value["id"], "result": {}}), flush=True)
        elif value.get("method") == "config/read":
            print(json.dumps({"id": value["id"], "result": {"config": json.loads(os.environ.get("FAKE_CODEX_CONFIG") or "{}")}}), flush=True)
        elif value.get("method") == "model/list":
            page = value.get("params", {}).get("cursor")
            data = [{"id": "m1", "model": "gpt-a", "displayName": "GPT-A", "isDefault": True, "defaultReasoningEffort": "low",
                     "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]},
                    {"id": "hidden", "model": "gpt-h", "hidden": True}] if not page else [{"model": "gpt-b", "displayName": "GPT-B"}]
            print(json.dumps({"id": value["id"], "result": {"data": data, "nextCursor": None if page else "p2"}}), flush=True)
''')


class ModelCatalogTests(unittest.TestCase):
    def test_cursor_groups_effort_variants_into_one_model(self):
        with patch.object(models, "_run", return_value=CURSOR):
            listed = models._cursor(["cursor-agent"])["models"]
        by_label = {m["label"]: m for m in listed}
        self.assertTrue(by_label["Auto"]["default"])
        grok = by_label["Grok 4.7"]
        self.assertEqual((grok["efforts"], grok["default_effort"], grok["id"]), (["low", "high", "xhigh"], "high", "grok-4.7-high"))
        self.assertEqual(grok["variants"]["xhigh"], "grok-4.7-xhigh", "the effort picks Cursor's own model id")
        self.assertEqual(by_label["Grok 4.7 Fast"]["variants"], {"low": "grok-4.7-low-fast"})
        self.assertEqual(by_label["DeepSeek V4.1 Flash 1017"]["efforts"], ["none", "high"])
        self.assertNotIn("variants", by_label["Composer 2.5"])

    def test_claude_reads_efforts_and_aliases_from_its_help(self):
        # Versions without initialize models fall back to --help.
        with patch.object(models, "_run", return_value=CLAUDE):
            value = models._claude_help(["claude"])
        self.assertEqual(value["efforts"], ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual([m["id"] for m in value["models"]], ["fable", "opus", "sonnet"])

    def test_claude_lists_the_models_its_initialize_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder, "claude.py")
            script.write_text(FAKE_CLAUDE, encoding="utf-8")
            value = models._claude([sys.executable, str(script)])
            argv = json.loads(Path(folder, "argv.json").read_text(encoding="utf-8"))
        self.assertIn('{"disableAllHooks": true}', argv, "No hook runs for a process that only lists models")
        self.assertEqual([(m["id"], m["label"], m["default"]) for m in value["models"]],
                         [("opus", "Opus", True), ("haiku", "Haiku", False), ("glm-5", "glm-5", False)],
                         "Claude Code's own default row is Desk's default; the model it resolves to is tagged")
        self.assertEqual(value["models"][1]["efforts"], [], "a model without effort support offers none")
        self.assertEqual(value["efforts"], ["low", "high", "max"])
        self.assertEqual(value["models"][0]["description"], "Opus 5.5 · Best for everyday, complex tasks")

    def test_codex_lists_models_from_its_app_server(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder, "codex.py")
            script.write_text(FAKE_CODEX, encoding="utf-8")
            value = models._codex([sys.executable, str(script)])
            self.assertEqual([(m["id"], m["label"], m["efforts"], m["default"]) for m in value["models"]],
                             [("gpt-a", "GPT-A", ["low", "high"], True), ("gpt-b", "GPT-B", [], False)], "hidden models are left out; pages are followed")
            # The configured model is what runs without --model: it is the default, with its effort.
            with patch.dict(os.environ, {"FAKE_CODEX_CONFIG": json.dumps({"model": "gpt-b", "model_reasoning_effort": "high"})}):
                value = models._codex([sys.executable, str(script)])
            self.assertEqual([(m["id"], m["default"], m.get("default_effort")) for m in value["models"]],
                             [("gpt-a", False, "low"), ("gpt-b", True, "high")])
            # A provider of the user's own is reached with its configured model only.
            own = {"model": "deepseek-v4", "model_provider": "ark", "model_providers": {"ark": {"name": "Volcengine Ark", "base_url": "https://example.invalid"}}}
            with patch.dict(os.environ, {"FAKE_CODEX_CONFIG": json.dumps(own)}):
                value = models._codex([sys.executable, str(script)])
            self.assertEqual(value["models"], [{"id": "deepseek-v4", "label": "deepseek-v4", "description": "Volcengine Ark", "efforts": [], "default": True}])
            self.assertEqual(value["efforts"], ["low", "high"], "Codex's levels stay available as free choices")

    def test_acp_agents_report_models_and_efforts_in_their_session(self):
        grok = models.acp_catalog("grok", {"models": {"currentModelId": "volcengine", "availableModels": [
            {"modelId": "grok-4.6", "name": "Grok 4.6", "_meta": {"reasoningEffort": "high", "reasoningEfforts": [
                {"value": "xhigh", "label": "Extra High Effort"}, {"value": "high", "label": "High Effort", "default": True}, {"value": "low", "label": "Low Effort"}]}},
            {"modelId": "volcengine", "name": "Volcengine Coding Plan", "_meta": {"agentType": "grok-build-plan"}}]},
            "configOptions": [{"id": "model", "category": "model", "currentValue": "volcengine", "options": [{"value": "grok-4.6", "name": "Grok 4.6"}, {"value": "volcengine", "name": "Volcengine Coding Plan"}]}]})
        self.assertEqual([(m["id"], m["efforts"], m.get("default_effort"), m["default"]) for m in grok["models"]],
                         [("grok-4.6", ["xhigh", "high", "low"], "high", False), ("volcengine", [], None, True)])
        self.assertEqual(grok["effort_labels"]["xhigh"], "Extra High", "the agent's own names for its levels")

        codebuddy = models.acp_catalog("codebuddy", {"models": {"currentModelId": "ark-code-latest", "availableModels": [
            {"modelId": "hy4free", "name": "Hy4", "description": "x0.00 credits", "_meta": {"effortOptions": [{"value": "high", "name": "High"}]}},
            {"modelId": "hy4std", "name": "Hy4", "description": "x0.29 credits", "_meta": {"effortOptions": [{"value": "high", "name": "High"}]}}]},
            "configOptions": [{"id": "thought_level", "category": "thought_level", "currentValue": "enabled",
                               "options": [{"value": "disabled", "name": "Off"}, {"value": "high", "name": "High"}, {"value": "enabled", "name": "On"}]}]})
        self.assertEqual([(m["id"], m["label"], m["efforts"], m["default"]) for m in codebuddy["models"]],
                         [("ark-code-latest", "ark-code-latest", ["disabled", "high", "enabled"], True),
                          ("hy4free", "Hy4 · x0.00 credits", ["high"], False), ("hy4std", "Hy4 · x0.29 credits", ["high"], False)],
                         "the current model (configured, not listed) leads with the session's levels; equal names keep their description")
        self.assertEqual(codebuddy["models"][0]["default_effort"], "enabled")
        self.assertEqual(codebuddy["effort_labels"]["disabled"], "Off")

        dsh = models.acp_catalog("dsh", {"configOptions": [
            {"id": "model", "category": "model", "currentValue": '["deepseek-official","deepseek-v4-flash"]',
             "options": [{"group": "deepseek-official", "name": "deepseek-official", "options": [{"value": '["deepseek-official","deepseek-v4-flash"]', "name": "deepseek-v4-flash"}]}]},
            {"id": "reasoning_effort", "category": "thought_level", "currentValue": "high", "options": [{"value": "off", "name": "Off"}, {"value": "high", "name": "High"}]}]})
        self.assertEqual(dsh["models"], [{"id": "deepseek-official/deepseek-v4-flash", "label": "deepseek-v4-flash", "efforts": ["off", "high"],
                                          "default": True, "default_effort": "high", "description": "deepseek-official"}],
                         "a DSH route is chosen as provider/model")

        opencode = models.acp_catalog("opencode", {"configOptions": [
            {"id": "model", "category": "model", "currentValue": "ark/code", "options": [{"value": "ark/code", "name": "ark/Code"}, {"value": "alibaba/qwen", "name": "alibaba/Qwen"}]},
            {"id": "effort", "category": "thought_level", "currentValue": "default", "options": [{"value": "low"}, {"value": "high"}, {"value": "default", "name": "Default"}]}]})
        self.assertEqual(opencode["efforts"], ["low", "high"], "the agent's own 'default' is Desk's default choice")
        self.assertEqual([(m["id"], m["efforts"], m.get("default_effort")) for m in opencode["models"]], [("ark/code", ["low", "high"], None), ("alibaba/qwen", [], None)])

    def test_acp_catalog_opens_one_session_through_the_agents_own_command(self):
        config = {"providers": {"kimi": {"command": [sys.executable, str(FIXTURE), "kimi", "selection-catalog"]}}}
        value = models.catalog(config, "kimi")
        self.assertEqual(value["source"], "native")
        self.assertEqual([(m["id"], m["efforts"], m["default"]) for m in value["models"]], [("native-model", ["deep-native", "light-native"], True)])
        failing = {"providers": {"kimi": {"command": [sys.executable, str(FIXTURE), "kimi", "auth-required"]}}}
        with self.assertRaises(models.SwarmError) as raised:
            models.catalog(failing, "kimi")
        self.assertEqual(raised.exception.code, "native_auth_required")

    def test_agents_without_a_catalog_get_free_text(self):
        self.assertEqual(models.catalog({}, "muse")["source"], "none")

if __name__ == "__main__":
    unittest.main()
