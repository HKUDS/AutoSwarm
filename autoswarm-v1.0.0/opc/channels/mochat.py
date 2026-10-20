from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from opc.channels.provider_base import OptionalDependencyChannel
from opc.core.models import SystemMessage


@dataclass(frozen=True)
class MochatTarget:
    id: str
    is_panel: bool


def resolve_mochat_target(raw: str) -> MochatTarget:
    trimmed = (raw or "").strip()
    if not trimmed:
        return MochatTarget(id="", is_panel=False)
    lowered = trimmed.lower()
    cleaned = trimmed
    forced_panel = False
    for prefix in ("mochat:", "group:", "channel:", "panel:"):
        if lowered.startswith(prefix):
            cleaned = trimmed[len(prefix):].strip()
            forced_panel = prefix in {"group:", "channel:", "panel:"}
            break
    return MochatTarget(id=cleaned, is_panel=forced_panel or not cleaned.startswith("session_"))


class MochatChannel(OptionalDependencyChannel):
    name = "mochat"
    required_package = "socketio"
    delivery_mode = "bridge"

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self._socket: Any = None
        self._http: httpx.AsyncClient | None = None

    def get_required_config_fields(self) -> list[str]:
        return ["base_url", "claw_token", "agent_user_id"]

    def normalize_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = payload.get("message", payload)
        return {
            "sender_id": str(message.get("from", message.get("sender_id", "")) or ""),
            "chat_id": str(message.get("conversation_id", message.get("room_id", "")) or ""),
            "content": str(message.get("text", message.get("content", "")) or ""),
            "thread_id": str(message.get("thread_id", "") or ""),
            "reply_to": str(message.get("reply_to", message.get("id", "")) or ""),
            "metadata": {"group_id": str(message.get("group_id", "") or "")},
        }

    async def start(self) -> None:
        await super().start()
        self._http = httpx.AsyncClient(base_url=self.config.base_url.rstrip("/"), timeout=30)
        self._runner_task = asyncio.create_task(self._run_with_restarts(self._runtime_loop, label="mochat"))

    async def _runtime_loop(self) -> None:
        try:
            if await self._start_socket_client():
                while self.is_running:
                    await asyncio.sleep(1)
                return
        except Exception as exc:
            self.set_last_error(exc)
        await self._fallback_loop()

    async def _start_socket_client(self) -> bool:
        import socketio

        client = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=self.config.max_retry_attempts or None,
            reconnection_delay=max(0.1, self.config.socket_reconnect_delay_ms / 1000.0),
            reconnection_delay_max=max(0.1, self.config.socket_max_reconnect_delay_ms / 1000.0),
            logger=False,
            engineio_logger=False,
        )

        @client.on("claw.session.events")
        async def on_session_events(payload: dict[str, Any]) -> None:
            await self.publish_normalized(self.normalize_event(payload))

        @client.on("claw.panel.events")
        async def on_panel_events(payload: dict[str, Any]) -> None:
            await self.publish_normalized(self.normalize_event(payload))

        socket_url = (self.config.socket_url or self.config.base_url).strip().rstrip("/")
        socket_path = (self.config.socket_path or "/socket.io").strip().lstrip("/")
        await client.connect(
            socket_url,
            transports=["websocket"],
            socketio_path=socket_path,
            auth={"token": self.config.claw_token},
            wait_timeout=max(1.0, self.config.socket_connect_timeout_ms / 1000.0),
        )
        self._socket = client
        if self.config.sessions:
            await client.call(
                "com.claw.im.subscribeSessions",
                {"sessionIds": list(self.config.sessions), "limit": self.config.watch_limit},
                timeout=10,
            )
        if self.config.panels:
            await client.call("com.claw.im.subscribePanels", {"panelIds": list(self.config.panels)}, timeout=10)
        return True

    async def _fallback_loop(self) -> None:
        while self.is_running:
            for session_id in list(self.config.sessions or []):
                try:
                    payload = await self._post_json(
                        "/api/claw/sessions/watch",
                        {"sessionId": session_id, "timeoutMs": self.config.watch_timeout_ms, "limit": self.config.watch_limit},
                    )
                    await self.publish_normalized(self.normalize_event(payload))
                except Exception as exc:
                    self.set_last_error(exc)
            await asyncio.sleep(max(1.0, self.config.refresh_interval_ms / 1000.0))

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert self._http is not None
        response = await self._http.post(path, json=payload, headers={"Authorization": f"Bearer {self.config.claw_token}"})
        response.raise_for_status()
        parsed = response.json()
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
            return parsed["data"]
        return parsed if isinstance(parsed, dict) else {}

    async def stop(self) -> None:
        if self._socket is not None:
            try:
                await self._socket.disconnect()
            except Exception:
                logger.exception("mochat socket close failed")
            self._socket = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        await super().stop()

    async def send(self, message: SystemMessage) -> None:
        await super().send(message)
        if self._http is None:
            logger.warning("mochat http client not initialized")
            return
        metadata = dict(message.metadata or {})
        target = resolve_mochat_target(str(metadata.get("chat_id") or message.session_id or ""))
        reply_to = str(metadata.get("reply_to", "") or "")
        if target.is_panel:
            payload: dict[str, Any] = {"panelId": target.id, "content": message.content}
            group_id = str(metadata.get("group_id", "") or "")
            if group_id:
                payload["groupId"] = group_id
            if reply_to:
                payload["replyTo"] = reply_to
            await self._post_json("/api/claw/groups/panels/send", payload)
            return
        payload = {"sessionId": target.id, "content": message.content}
        if reply_to:
            payload["replyTo"] = reply_to
        await self._post_json("/api/claw/sessions/send", payload)
