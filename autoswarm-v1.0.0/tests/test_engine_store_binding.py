from __future__ import annotations

import unittest
import asyncio
from types import SimpleNamespace

from opc.engine import OPCEngine


class _StubStore:
    def __init__(self, name: str) -> None:
        self.name = name
        self.close_calls = 0

    async def save_task(self, task: object) -> None:
        return None

    async def save_runtime_session(self, session: object) -> None:
        return None

    async def close(self) -> None:
        self.close_calls += 1


class EngineStoreBindingTests(unittest.TestCase):
    def test_bind_store_rebinds_cached_store_references(self) -> None:
        engine = OPCEngine()
        old_store = _StubStore("old")
        new_store = _StubStore("new")

        engine.store = old_store
        engine.memory = SimpleNamespace(store=old_store)
        engine.org_engine = SimpleNamespace(store=old_store)
        engine.task_scheduler = SimpleNamespace(store=old_store)
        engine.communication = SimpleNamespace(store=old_store)
        engine.context_assembler = SimpleNamespace(store=old_store)
        engine.approval_engine = SimpleNamespace(store=old_store)
        engine.external_broker = SimpleNamespace(store=old_store)
        engine.secretary = SimpleNamespace(store=old_store)
        engine.reorg_manager = SimpleNamespace(store=old_store)
        engine.cost_tracker = SimpleNamespace(store=old_store)
        engine.context_loader = SimpleNamespace(store=old_store)
        engine.heartbeat_scheduler = SimpleNamespace(store=old_store)
        engine.company_executor = SimpleNamespace(save_task=old_store.save_task)

        engine.bind_store(new_store)

        self.assertIs(engine.store, new_store)
        self.assertIs(engine.memory.store, new_store)
        self.assertIs(engine.org_engine.store, new_store)
        self.assertIs(engine.task_scheduler.store, new_store)
        self.assertIs(engine.communication.store, new_store)
        self.assertIs(engine.context_assembler.store, new_store)
        self.assertIs(engine.approval_engine.store, new_store)
        self.assertIs(engine.external_broker.store, new_store)
        self.assertIs(engine.secretary.store, new_store)
        self.assertIs(engine.reorg_manager.store, new_store)
        self.assertIs(engine.cost_tracker.store, new_store)
        self.assertIs(engine.context_loader.store, new_store)
        self.assertIs(engine.heartbeat_scheduler.store, new_store)
        self.assertIs(engine.company_executor.save_task.__self__, new_store)

    def test_shutdown_does_not_close_unowned_shared_store(self) -> None:
        shared_store = _StubStore("shared")
        engine = OPCEngine(store=shared_store, owns_store=False)
        engine.comms_reactivation_sweeper = None
        engine.heartbeat_scheduler = None
        engine.channel_manager = None
        engine.mcp_manager = None
        engine.llm = None

        asyncio.run(engine.shutdown())

        self.assertEqual(shared_store.close_calls, 0)


if __name__ == "__main__":
    unittest.main()
