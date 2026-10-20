from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opc.core.config import ExternalAgentConfig, OPCConfig
from opc.core.models import AgentStatus, Task, TaskResult, TaskStatus
from opc.layer2_organization.approval import ApprovalEngine
from opc.layer3_agent.adapters.codex_adapter import CodexAdapter
from opc.layer3_agent.adapters.base import ExternalAgentAdapter
from opc.layer3_agent.external_broker import ExternalAgentBroker
from opc.layer3_agent.preflight import (
    ExternalAgentPreflightResult,
    _describe_collaboration_rpc_transport,
    _describe_stdin_policy,
    _missing_agent_issue,
    probe_external_agent_write_contract,
    run_external_agent_preflight,
)


class _NoopAdapter(ExternalAgentAdapter):
    agent_type = "noop"
    default_command = "noop"

    async def is_available(self) -> bool:
        return True

    async def execute(self, task: Task, workspace_path: str) -> TaskResult:
        return TaskResult(status=TaskStatus.DONE, content="ok")

    def build_invocation(self, task: Task, workspace_path: str | None = None):
        return ["noop", "run"], self.build_invocation_metadata(["noop", "run"])

    async def get_status(self) -> AgentStatus:
        return AgentStatus.IDLE


class _ApprovalStub(ApprovalEngine):
    def __init__(self) -> None:
        self.called = False

    async def authorize_external_action(self, *args, **kwargs):
        self.called = True
        raise AssertionError("approval should not run after a failed workspace preflight")


class ExternalAgentPreflightTests(unittest.IsolatedAsyncioTestCase):
    def test_preflight_result_serializes_stdin_policy(self) -> None:
        result = ExternalAgentPreflightResult(
            agent="codex",
            enabled=True,
            command="codex",
            available=True,
            stdin_policy="devnull",
            collaboration_rpc_transport="tcp(loopback)",
        )

        self.assertEqual(result.as_dict()["stdin_policy"], "devnull")
        self.assertEqual(result.as_dict()["collaboration_rpc_transport"], "tcp(loopback)")

    def test_describe_collaboration_rpc_transport_uses_tcp_when_fifo_unavailable(self) -> None:
        with patch.dict("os.environ", {}, clear=True), patch(
            "opc.layer4_tools.collaboration_rpc.fifo_rpc_supported",
            return_value=False,
        ):
            transport, issue = _describe_collaboration_rpc_transport()

        self.assertEqual(transport, "tcp(loopback)")
        self.assertEqual(issue, "")

    def test_describe_collaboration_rpc_transport_reports_forced_fifo_unavailable(self) -> None:
        with patch.dict("os.environ", {"OPC_COLLAB_RPC_TRANSPORT": "fifo"}, clear=False), patch(
            "opc.layer4_tools.collaboration_rpc.fifo_rpc_supported",
            return_value=False,
        ):
            transport, issue = _describe_collaboration_rpc_transport()

        self.assertEqual(transport, "fifo(unavailable)")
        self.assertIn("FIFO collaboration RPC is unavailable", issue)

    def test_describe_stdin_policy_defaults_to_devnull(self) -> None:
        adapter = _NoopAdapter(config=ExternalAgentConfig(command="noop"))

        self.assertEqual(_describe_stdin_policy(adapter, ["noop", "run"], {}), "devnull")

    def test_describe_stdin_policy_uses_adapter_policy_not_stale_metadata(self) -> None:
        adapter = _NoopAdapter(config=ExternalAgentConfig(command="noop"))

        self.assertEqual(
            _describe_stdin_policy(adapter, ["noop", "run"], {"stdin_policy": "inherit"}),
            "devnull",
        )

    def test_describe_stdin_policy_reports_codex_no_pty_argv_as_inherit(self) -> None:
        adapter = CodexAdapter(config=ExternalAgentConfig(command="codex"))
        cmd = ["codex", "exec", "--json", "hello"]

        with patch.object(CodexAdapter, "_supports_pty_input_channel", return_value=False):
            policy = _describe_stdin_policy(adapter, cmd, {"prompt_transport": "argv"})

        self.assertEqual(policy, "inherit")

    def test_missing_cursor_agent_reports_editor_cli_separately(self) -> None:
        def _which(name: str) -> str | None:
            if name == "cursor":
                return "/usr/local/bin/cursor"
            return None

        with patch("shutil.which", side_effect=_which):
            issue = _missing_agent_issue("cursor", "cursor-agent")

        self.assertIn("Cursor editor found, cursor-agent missing", issue)

    def test_external_agent_preflight_reports_cursor_editor_only_and_stdin_policy(self) -> None:
        config = OPCConfig()
        for name, agent_config in config.agents.agents.items():
            agent_config.enabled = name == "cursor"
        config.agents.agents["cursor"].command = "cursor"

        def _which(name: str) -> str | None:
            if name == "cursor":
                return "/usr/local/bin/cursor"
            return None

        with tempfile.TemporaryDirectory() as tmpdir, patch("shutil.which", side_effect=_which):
            root = Path(tmpdir)
            results = run_external_agent_preflight(
                config,
                workspace_path=root / "workspace",
                opc_home=root / ".opc",
                probe_commands=False,
                prepare_surfaces=False,
            )

        cursor = next(item for item in results if item.agent == "cursor")
        self.assertFalse(cursor.available)
        self.assertEqual(cursor.stdin_policy, "devnull")
        self.assertIn("cursor", cursor.launch_command)
        self.assertTrue(
            any("Cursor editor found, cursor-agent missing" in issue for issue in cursor.issues)
        )

    def test_write_contract_reports_blocked_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            blocked_workspace = root / "workspace-file"
            blocked_workspace.write_text("not a directory", encoding="utf-8")

            checks = probe_external_agent_write_contract(
                workspace_path=blocked_workspace,
                opc_home=root / ".opc",
                project_db_path=root / ".opc" / "projects" / "default" / "tasks.db",
            )

            workspace = next(check for check in checks if check.name == "workspace")
            self.assertFalse(workspace.ok)
            self.assertIn("workspace-file", workspace.path)

    async def test_broker_fails_before_launch_when_workspace_contract_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            blocked_workspace = root / "workspace-file"
            blocked_workspace.write_text("not a directory", encoding="utf-8")
            approval = _ApprovalStub()
            broker = ExternalAgentBroker(SimpleNamespace(db_path=""), approval)
            adapter = _NoopAdapter(config=ExternalAgentConfig(command="noop"))

            result = await broker.run(
                adapter=adapter,
                task=Task(title="blocked", project_id="default"),
                workspace_path=str(blocked_workspace),
            )

            self.assertEqual(result.status, TaskStatus.FAILED)
            self.assertIn("workspace permission preflight failed", result.content)
            self.assertFalse(approval.called)
            contract = result.artifacts["workspace_permission_contract"]
            self.assertTrue(any(item["name"] == "workspace" and not item["ok"] for item in contract))
