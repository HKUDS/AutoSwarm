from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opc.core.config import OPCConfig, RoleConfig, RoleRuntimePolicyConfig
from opc.layer2_organization.org_engine import OrgEngine


class BuiltinRoleOverrideTests(unittest.TestCase):
    def test_corporate_builtin_roles_inherit_configured_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = OPCConfig()
            config.org.company_profile = "corporate"
            config.org.roles = [
                RoleConfig(
                    id="ceo",
                    name="CEO",
                    responsibility="Configured CEO role",
                    tools=["file_read", "todo_write"],
                    runtime_policy=RoleRuntimePolicyConfig(execution_strategy="native"),
                ),
                RoleConfig(
                    id="cto",
                    name="CTO",
                    responsibility="Configured CTO role",
                    tools=["shell_exec", "web_search"],
                    runtime_policy=RoleRuntimePolicyConfig(execution_strategy="native"),
                ),
            ]

            engine = OrgEngine(config, Path(tmpdir))

            ceo = engine.get_agent("ceo")
            cto = engine.get_agent("cto")
            cmo = engine.get_agent("cmo")
            content = engine.get_agent("content_specialist")
            design = engine.get_agent("designer")
            qa = engine.get_agent("qa_analyst")
            env = engine.get_agent("env_engineer")

            self.assertIsNotNone(ceo)
            self.assertIsNotNone(cto)
            self.assertIsNotNone(cmo)
            self.assertIsNotNone(content)
            self.assertIsNotNone(design)
            self.assertIsNotNone(qa)
            self.assertIsNotNone(env)
            assert ceo is not None
            assert cto is not None
            assert cmo is not None
            assert content is not None
            assert design is not None
            assert qa is not None
            assert env is not None

            self.assertEqual(len(engine.list_agents()), 11)
            self.assertIn("file_read", ceo.tools)
            self.assertIn("todo_write", ceo.tools)
            self.assertNotIn("request_user_input", ceo.tools)
            self.assertEqual(ceo.runtime_policy["execution_strategy"], "native")
            self.assertIn("shell_exec", cto.tools)
            self.assertIn("web_search", cto.tools)
            self.assertIn("file_read", cmo.tools)
            self.assertIn("web_search", cmo.tools)
            self.assertIn("shell_exec", content.tools)
            self.assertIn("file_write", content.tools)
            self.assertIn("shell_exec", design.tools)
            self.assertIn("file_write", design.tools)
            self.assertIn("shell_exec", qa.tools)
            self.assertIn("file_write", qa.tools)
            self.assertIn("shell_exec", env.tools)
            self.assertIn("file_write", env.tools)

    def test_custom_org_empty_tools_remain_unconfigured(self) -> None:
        config = OPCConfig()
        config.org.company_profile = "custom"
        config.org.roles = [
            RoleConfig(
                id="lead",
                name="Lead",
                responsibility="Coordinate.",
                tools=[],
            ),
            RoleConfig(
                id="analyst",
                name="Analyst",
                responsibility="Analyze.",
                reports_to="lead",
                tools=[],
            ),
        ]

        engine = OrgEngine(config)
        lead = engine.get_agent("lead")
        analyst = engine.get_agent("analyst")

        assert lead is not None
        assert analyst is not None
        self.assertEqual(lead.tools, [])
        self.assertEqual(analyst.tools, [])


if __name__ == "__main__":
    unittest.main()
