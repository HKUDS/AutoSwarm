from __future__ import annotations

import unittest

from opc.core.models import ExecutionMode
from opc.layer1_perception.context_loader import LoadedContext
from opc.layer1_perception.task_router import TaskRouter


class _StubLLM:
    def __init__(self) -> None:
        self.called = False

    async def simple_chat(self, prompt: str, system: str, task_type: str) -> str:
        _ = (prompt, system, task_type)
        self.called = True
        return "{}"


class TaskRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_returns_default_task_mode_without_llm_call(self) -> None:
        llm = _StubLLM()
        router = TaskRouter(llm)

        decision = await router.route("build a local product", LoadedContext())

        self.assertEqual(decision.mode, ExecutionMode.TASK_MODE)
        self.assertEqual(decision.domains, ["general"])
        self.assertFalse(llm.called)

    async def test_route_ignores_context_and_preferences(self) -> None:
        llm = _StubLLM()
        router = TaskRouter(llm)
        context = LoadedContext()
        context.session_execution_defaults = {
            "mode": "company_mode",
            "company_profile": "corporate",
            "preferred_agent": "codex",
        }

        decision = await router.route(
            "continue the current project",
            context,
            preferences={"mode": "company_mode"},
        )

        self.assertEqual(decision.mode, ExecutionMode.TASK_MODE)
        self.assertEqual(decision.domains, ["general"])
        self.assertFalse(llm.called)


if __name__ == "__main__":
    unittest.main()
