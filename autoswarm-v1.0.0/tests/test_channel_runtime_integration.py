from __future__ import annotations

import contextlib
import shutil
import unittest
import uuid
from pathlib import Path

from opc.channels.email import EmailChannel
from opc.channels.slack import SlackChannel
from opc.channels.telegram import TelegramChannel
from opc.channels.whatsapp import WhatsAppChannel
from opc.core.config import OPCConfig
from opc.core.models import SystemMessage
from opc.layer0_interaction.message_bus import MessageBus


@contextlib.contextmanager
def _workspace_tempdir() -> Path:
    base = Path.cwd() / ".tmp-test" / f"channel-runtime-{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


class ChannelProviderRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_email_send_sets_reply_headers(self) -> None:
        config = OPCConfig()
        config.channels.email.consent_granted = True
        config.channels.email.imap_host = "imap.example.com"
        config.channels.email.imap_username = "bot@example.com"
        config.channels.email.imap_password = "secret"
        config.channels.email.smtp_host = "smtp.example.com"
        config.channels.email.smtp_username = "bot@example.com"
        config.channels.email.smtp_password = "secret"
        channel = EmailChannel(config.channels.email, MessageBus())
        channel._last_subject_by_chat["user@example.com"] = "Original subject"
        channel._last_message_id_by_chat["user@example.com"] = "<abc@example.com>"
        sent = {}

        def fake_smtp_send(message):
            sent["message"] = message

        channel._smtp_send = fake_smtp_send  # type: ignore[method-assign]
        await channel.send(
            SystemMessage(
                channel="email",
                user_id="u",
                session_id="sess",
                content="hello",
                metadata={"chat_id": "user@example.com"},
            )
        )

        message = sent["message"]
        self.assertEqual(message["To"], "user@example.com")
        self.assertEqual(message["In-Reply-To"], "<abc@example.com>")
        self.assertEqual(message["References"], "<abc@example.com>")

    async def test_telegram_send_splits_messages_and_uses_reply(self) -> None:
        config = OPCConfig()
        config.channels.telegram.reply_to_message = True
        channel = TelegramChannel(config.channels.telegram, MessageBus())
        calls = []

        class _Bot:
            async def send_message(self, **kwargs):
                calls.append(("message", kwargs))

            async def send_document(self, **kwargs):
                calls.append(("document", kwargs))

        channel._bot = _Bot()
        with _workspace_tempdir() as tmpdir:
            attachment = tmpdir / "a.txt"
            attachment.write_text("demo")
            await channel.send(
                SystemMessage(
                    channel="telegram",
                    user_id="u",
                    session_id="7",
                    content="x" * 4500,
                    metadata={"chat_id": "7", "reply_to": "11", "attachments": [str(attachment)]},
                )
            )

        self.assertEqual(calls[0][0], "message")
        self.assertEqual(calls[0][1]["reply_to_message_id"], 11)
        self.assertEqual(calls[-1][0], "document")

    async def test_slack_socket_event_produces_thread_scoped_session(self) -> None:
        config = OPCConfig()
        config.channels.slack.allow_from = ["*"]
        bus = MessageBus()
        channel = SlackChannel(config.channels.slack, bus)
        channel._bot_user_id = "bot-1"

        class _Client:
            async def send_socket_mode_response(self, response):
                return response

        class _Req:
            type = "events_api"
            envelope_id = "env-1"
            payload = {
                "event": {
                    "type": "app_mention",
                    "user": "u1",
                    "channel": "c1",
                    "text": "<@bot-1> hi",
                    "thread_ts": "th-1",
                    "ts": "msg-1",
                    "channel_type": "channel",
                }
            }

        await channel._on_socket_request(_Client(), _Req())
        message = await bus._inbound_queue.get()
        self.assertEqual(message.session_id, "slack:c1:th-1")
        self.assertEqual(message.content, "hi")

    async def test_whatsapp_bridge_message_publishes_inbound(self) -> None:
        config = OPCConfig()
        config.channels.whatsapp.allow_from = ["*"]
        bus = MessageBus()
        channel = WhatsAppChannel(config.channels.whatsapp, bus)

        await channel._handle_bridge_message(
            '{"type":"message","id":"m1","sender":"12345@s.whatsapp.net","message":{"chat_id":"12345@s.whatsapp.net","text":"hello"}}'
        )

        message = await bus._inbound_queue.get()
        self.assertEqual(message.user_id, "12345")
        self.assertEqual(message.metadata["chat_id"], "12345@s.whatsapp.net")
