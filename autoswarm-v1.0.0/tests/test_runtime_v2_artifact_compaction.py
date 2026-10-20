from __future__ import annotations

import unittest

from opc.core.config import OPCConfig
from opc.core.models import Task
from opc.layer3_agent.runtime_v2.runtime import NativeRuntimeV2
from opc.layer4_tools.registry import ToolRegistry


class _StubLLM:
    def __init__(self) -> None:
        self.config = type("Cfg", (), {"max_tokens": 4096})()


class RuntimeArtifactCompactionTests(unittest.TestCase):
    def test_reinject_runtime_artifacts_adds_structured_state_messages(self) -> None:
        runtime = NativeRuntimeV2(
            llm=_StubLLM(),
            tool_registry=ToolRegistry(),
            config=OPCConfig(),
        )
        task = Task(
            id="task-artifacts",
            session_id="sess-artifacts",
            project_id="proj1",
            metadata={
                "_prompt_harness_boot_artifacts": [
                    {
                        "type": "tool_surface_delta",
                        "title": "Tool Surface",
                        "content": "Current runtime tool surface:\n- Count: 2",
                        "scope": "runtime",
                        "content_hash": "boot-hash",
                        "metadata": {},
                    }
                ],
                "prompt_harness": {"artifact_hashes": {"tool_surface_delta": "boot-hash"}},
            },
        )
        messages = [
            {"role": "system", "content": "You are a test runtime."},
            {"role": "system", "content": "## Session Memory\nRemember the current plan."},
            {"role": "assistant", "content": "Done."},
        ]
        todo_state = [{"content": "Patch runtime", "active_form": "Patching runtime", "status": "in_progress"}]
        runtime_notes = {
            "verification": {"completed": True, "passed": True, "status_line": "Verification: verified by verify."},
            "permission_details": [{"tool_name": "shell_exec", "resolution": "ask", "risk_level": "medium"}],
            "artifact_manifest": [],
        }

        updated = runtime._reinject_runtime_artifacts(
            messages,
            task=task,
            todo_state=todo_state,
            runtime_notes=runtime_notes,
            compaction_boundaries=[],
            active_subagents=[{"agent_id": "na_1", "name": "worker", "status": "running", "description": "Inspect repo"}],
        )

        artifact_messages = [
            item for item in updated
            if item["role"] == "system" and str(item["content"]).startswith("## Runtime Artifact")
        ]
        self.assertTrue(artifact_messages)
        self.assertTrue(any("Task Ledger" in item["content"] for item in artifact_messages))
        self.assertTrue(any("Active Subagents" in item["content"] for item in artifact_messages))
        self.assertTrue(any("Verification" in item["content"] for item in artifact_messages))
        self.assertTrue(runtime_notes["artifact_manifest"])
