from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from opc.channels.manager import ChannelManager
from opc.channels.provider_base import PollingChannel, SocketChannel
from opc.channels.provider_registry import PROVIDER_SPECS, ordered_provider_specs
from opc.core.config import OPCConfig
from opc.layer0_interaction.message_bus import MessageBus


class _PollingProbe(PollingChannel):
    name = "poll-probe"
    required_package = None

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self.calls = 0

    def get_poll_interval_seconds(self) -> float:
        return 0.01

    async def poll_once(self) -> None:
        self.calls += 1
        if self.calls >= 2:
            self.mark_stopped()


class _SocketProbe(SocketChannel):
    name = "socket-probe"
    required_package = None

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self.calls = 0

    async def run_socket_forever(self) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        self.mark_stopped()


class ChannelProviderRegistryTests(unittest.TestCase):
    def test_provider_registry_covers_all_builtin_channels(self) -> None:
        self.assertEqual(
            [spec.name for spec in ordered_provider_specs()],
            ["telegram", "whatsapp", "discord", "feishu", "mochat", "dingtalk", "email", "slack", "qq", "matrix"],
        )
        self.assertTrue(PROVIDER_SPECS["whatsapp"].bridge_required)
        self.assertEqual(PROVIDER_SPECS["slack"].extra_name, "channels-slack")


class ChannelRuntimeScaffoldTests(unittest.IsolatedAsyncioTestCase):
    async def test_polling_channel_runs_loop_until_stopped(self) -> None:
        channel = _PollingProbe(SimpleNamespace(allow_from=["*"]), MessageBus())
        await channel.start()
        await asyncio.sleep(0.05)
        await channel.stop()
        self.assertGreaterEqual(channel.calls, 2)

    async def test_socket_channel_retries_after_failure(self) -> None:
        channel = _SocketProbe(SimpleNamespace(allow_from=["*"]), MessageBus())
        channel.reconnect_delay_seconds = 0.01
        await channel.start()
        await asyncio.sleep(0.05)
        await channel.stop()
        self.assertGreaterEqual(channel.calls, 2)
        self.assertEqual(channel.last_error, "boom")

    async def test_manager_status_reports_bridge_and_missing_config(self) -> None:
        config = OPCConfig()
        config.channels.slack.enabled = True
        config.channels.slack.allow_from = ["*"]
        config.channels.whatsapp.enabled = True
        manager = ChannelManager(config, MessageBus())
        slack_status = manager.get_status("slack")
        whatsapp_status = manager.get_status("whatsapp")
        self.assertTrue(slack_status["enabled"])
        self.assertFalse(slack_status["configured"])
        self.assertIn("bot_token", slack_status["missing_config"])
        self.assertTrue(whatsapp_status["bridge_required"])
        self.assertTrue(whatsapp_status["enabled"])
        self.assertTrue(whatsapp_status["configured"])
