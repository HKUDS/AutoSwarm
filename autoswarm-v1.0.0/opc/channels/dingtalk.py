from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from loguru import logger

from opc.channels.provider_base import OptionalDependencyChannel
from opc.core.models import SystemMessage


class _DingTalkHandler:
    def __init__(self, channel: "DingTalkChannel"):
        self.channel = channel

    async def process(self, message: Any) -> None:
        chatbot = self.channel._parse_chatbot_message(message)
        if chatbot is None:
            return
        await self.channel._on_message(
            content=str(getattr(getattr(chatbot, "text", None), "content", "") or getattr(chatbot, "content", "") or ""),
            sender_id=str(getattr(chatbot, "sender_staff_id", "") or ""),
            sender_name=str(getattr(chatbot, "sender_nick", "") or getattr(chatbot, "sender_staff_id", "") or ""),
        )


class DingTalkChannel(OptionalDependencyChannel):
    name = "dingtalk"
    required_package = "dingtalk_stream"
    delivery_mode = "socket"

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self._client: Any = None
        self._http: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

    def get_required_config_fields(self) -> list[str]:
        return ["client_id", "client_secret"]

    def normalize_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        sender_id = str(payload.get("senderStaffId", "") or payload.get("sender_id", "") or "")
        return {
            "sender_id": sender_id,
            "chat_id": sender_id,
            "content": str(payload.get("text", {}).get("content", "") or payload.get("content", "") or ""),
            "thread_id": "",
            "reply_to": str(payload.get("msgId", "") or ""),
            "metadata": {"sender_name": str(payload.get("senderNick", "") or "")},
        }

    async def start(self) -> None:
        await super().start()
        from dingtalk_stream import Credential, DingTalkStreamClient
        from dingtalk_stream.chatbot import ChatbotMessage

        self._http = httpx.AsyncClient(timeout=30)
        self._client = DingTalkStreamClient(Credential(self.config.client_id, self.config.client_secret))
        self._client.register_callback_handler(ChatbotMessage.TOPIC, _DingTalkHandler(self))
        self._runner_task = asyncio.create_task(self._run_with_restarts(self._client.start, label="stream"))

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        await super().stop()

    def _parse_chatbot_message(self, message: Any) -> Any:
        try:
            from dingtalk_stream.chatbot import ChatbotMessage

            data = getattr(message, "data", None)
            if isinstance(data, dict):
                return ChatbotMessage.from_dict(data)
        except Exception:
            return None
        return None

    async def _get_access_token(self) -> str | None:
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token
        if self._http is None:
            return None
        resp = await self._http.post(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={"appKey": self.config.client_id, "appSecret": self.config.client_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data.get("accessToken")
        self._token_expiry = time.time() + int(data.get("expireIn", 7200)) - 60
        return self._access_token

    async def _send_markdown_text(self, token: str, chat_id: str, content: str) -> None:
        assert self._http is not None
        url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
        headers = {"x-acs-dingtalk-access-token": token}
        payload = {"robotCode": self.config.client_id, "userIds": [chat_id], "msgKey": "sampleMarkdown", "msgParam": {"title": "AutoSwarm", "text": content}}
        resp = await self._http.post(url, json=payload, headers=headers)
        resp.raise_for_status()

    async def _on_message(self, content: str, sender_id: str, sender_name: str) -> None:
        payload = self.normalize_event({"content": content, "sender_id": sender_id, "senderNick": sender_name})
        await self.publish_normalized(payload)

    async def send(self, message: SystemMessage) -> None:
        await super().send(message)
        token = await self._get_access_token()
        if not token:
            logger.warning("dingtalk token unavailable")
            return
        chat_id = str((message.metadata or {}).get("chat_id") or message.session_id or "")
        if message.content.strip():
            await self._send_markdown_text(token, chat_id, message.content.strip())
