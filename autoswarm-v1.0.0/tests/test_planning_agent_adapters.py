from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from opc.core.config import ExternalAgentConfig
from opc.core.models import TaskStatus
from opc.layer3_agent.adapters.claude_code import ClaudeCodeAdapter
from opc.layer3_agent.adapters.cursor_adapter import CursorAdapter
from opc.layer3_agent.adapters.jiuwen_adapter import JiuwenAdapter, JiuwenSwarmAdapter
from opc.layer3_agent.adapters.opencode_adapter import OpenCodeAdapter


@pytest.mark.parametrize("adapter_class", [ClaudeCodeAdapter, CursorAdapter, OpenCodeAdapter, JiuwenAdapter, JiuwenSwarmAdapter])
def test_planning_uses_selected_adapter_model_and_separate_process(tmp_path, monkeypatch, adapter_class):
    config = ExternalAgentConfig(model="user-selected-model", model_flag="--model", project_dir="/original-project", session_id="original-worker-session")
    adapter = adapter_class(config=config)
    worker_process = object()
    adapter._process = worker_process
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"ok":true}', b"")))
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(adapter_class, "start_process", launch)
    result = asyncio.run(adapter.plan_json("Return the team and hiring plan as JSON.", str(tmp_path)))
    command = launch.await_args.args[0]
    assert launch.await_args.args[1] == str(tmp_path)
    assert "original-worker-session" not in command
    assert adapter._process is worker_process
    assert adapter.config.project_dir == "/original-project"
    assert adapter.config.session_id == "original-worker-session"
    assert result.status == TaskStatus.DONE
    assert result.content == '{"ok":true}'
    assert result.artifacts["planning_agent"] == adapter.agent_type
    if adapter.agent_type not in {"jiuwen", "jiuwenswarm"}:
        assert "user-selected-model" in command


def test_stopping_external_planner_terminates_its_process_group(tmp_path, monkeypatch):
    async def run():
        started = asyncio.Event()
        async def communicate():
            started.set()
            await asyncio.Event().wait()
        process = SimpleNamespace(returncode=None, pid=456789, communicate=communicate, wait=AsyncMock())
        monkeypatch.setattr(CursorAdapter, "start_process", AsyncMock(return_value=process))
        kills = []
        monkeypatch.setattr("os.killpg", lambda pid, signal: kills.append(pid))
        task = asyncio.create_task(CursorAdapter().plan_json("Plan only.", str(tmp_path)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert kills == [process.pid]
        process.wait.assert_awaited_once()
    asyncio.run(run())
