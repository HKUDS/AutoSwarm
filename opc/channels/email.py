from __future__ import annotations

import asyncio
import html
import imaplib
import smtplib
import ssl
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Any

from loguru import logger

from opc.channels.provider_base import PollingChannel
from opc.core.models import SystemMessage
from opc.layer4_tools.output_budget import clip_text, persist_tool_result


class EmailChannel(PollingChannel):
    name = "email"
    required_package = None

    def __init__(self, config: Any, bus: Any):
        super().__init__(config, bus)
        self._processed_uids: set[str] = set()
        self._last_subject_by_chat: dict[str, str] = {}
        self._last_message_id_by_chat: dict[str, str] = {}

    def get_required_config_fields(self) -> list[str]:
        return [
            "imap_host",
            "imap_username",
            "imap_password",
            "smtp_host",
            "smtp_username",
            "smtp_password",
        ]

    def get_poll_interval_seconds(self) -> float:
        return max(5.0, float(self.config.poll_interval_seconds))

    async def start(self) -> None:
        if not self.config.consent_granted:
            raise RuntimeError("email channel requires `consent_granted: true` before polling or sending")
        await super().start()

    def normalize_email(self, payload: dict[str, Any]) -> dict[str, Any]:
        sender = str(payload.get("from", "") or "")
        thread_id = str(payload.get("thread_id", "") or "")
        return {
            "sender_id": sender,
            "chat_id": thread_id or sender,
            "content": str(payload.get("body", "") or payload.get("subject", "")),
            "thread_id": thread_id,
            "reply_to": str(payload.get("message_id", "") or ""),
            "metadata": {
                "email_subject": str(payload.get("subject", "") or ""),
                "email_from": sender,
                "body_truncated": bool(payload.get("body_truncated", False)),
                "body_omitted_chars": int(payload.get("body_omitted_chars", 0) or 0),
                "full_body_path": str(payload.get("full_body_path", "") or ""),
            },
        }

    async def poll_once(self) -> None:
        messages = await asyncio.to_thread(self._fetch_new_messages)
        for item in messages:
            sender = str(item.get("from", "") or "")
            if sender:
                subject = str(item.get("subject", "") or "")
                if subject:
                    self._last_subject_by_chat[sender] = subject
                message_id = str(item.get("message_id", "") or "")
                if message_id:
                    self._last_message_id_by_chat[sender] = message_id
            await self.publish_normalized(self.normalize_email(item))

    async def send(self, message: SystemMessage) -> None:
        await super().send(message)
        if not self.config.consent_granted:
            logger.warning("skip email send because consent_granted is false")
            return
        to_addr = str((message.metadata or {}).get("chat_id") or message.session_id or "").strip()
        if not to_addr:
            logger.warning("email outbound missing recipient address")
            return

        metadata = dict(message.metadata or {})
        is_reply = to_addr in self._last_subject_by_chat
        force_send = bool(metadata.get("force_send"))
        if is_reply and not self.config.auto_reply_enabled and not force_send:
            logger.info("skip automatic email reply to {} because auto_reply_enabled is false", to_addr)
            return

        base_subject = self._last_subject_by_chat.get(to_addr, "AutoSwarm reply")
        subject = str(metadata.get("subject", "") or "").strip() or self._reply_subject(base_subject)
        email_msg = EmailMessage()
        email_msg["From"] = self.config.from_address or self.config.smtp_username or self.config.imap_username
        email_msg["To"] = to_addr
        email_msg["Subject"] = subject
        email_msg.set_content(message.content or "")

        in_reply_to = str(metadata.get("reply_to") or self._last_message_id_by_chat.get(to_addr, "") or "")
        if in_reply_to:
            email_msg["In-Reply-To"] = in_reply_to
            email_msg["References"] = in_reply_to

        await asyncio.to_thread(self._smtp_send, email_msg)

    def _smtp_send(self, email_msg: EmailMessage) -> None:
        timeout = 30
        if self.config.smtp_use_ssl:
            with smtplib.SMTP_SSL(self.config.smtp_host, self.config.smtp_port, timeout=timeout) as smtp:
                smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(email_msg)
            return
        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=timeout) as smtp:
            if self.config.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.config.smtp_username, self.config.smtp_password)
            smtp.send_message(email_msg)

    def _connect_imap(self) -> Any:
        if self.config.imap_use_ssl:
            return imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        return imaplib.IMAP4(self.config.imap_host, self.config.imap_port)

    def _fetch_new_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        client = self._connect_imap()
        try:
            client.login(self.config.imap_username, self.config.imap_password)
            status, _ = client.select(self.config.imap_mailbox or "INBOX")
            if status != "OK":
                return messages
            status, data = client.search(None, "UNSEEN")
            if status != "OK" or not data:
                return messages
            for imap_id in data[0].split():
                status, fetched = client.fetch(imap_id, "(BODY.PEEK[] UID)")
                if status != "OK" or not fetched:
                    continue
                uid = self._extract_uid(fetched)
                if uid and uid in self._processed_uids:
                    continue
                raw_bytes = self._extract_message_bytes(fetched)
                if not raw_bytes:
                    continue
                parsed = self._parse_message_bytes(raw_bytes)
                if not parsed:
                    continue
                if uid:
                    self._processed_uids.add(uid)
                messages.append(parsed)
                if self.config.mark_seen:
                    client.store(imap_id, "+FLAGS", "\\Seen")
        finally:
            try:
                client.logout()
            except Exception:
                pass
        return messages

    @staticmethod
    def _extract_uid(fetched: list[Any]) -> str:
        for item in fetched:
            if not isinstance(item, tuple):
                continue
            header = item[0]
            if isinstance(header, bytes):
                text = header.decode("utf-8", errors="ignore")
                if "UID " in text:
                    return text.split("UID ", 1)[1].split(")", 1)[0].strip()
        return ""

    @staticmethod
    def _extract_message_bytes(fetched: list[Any]) -> bytes:
        for item in fetched:
            if isinstance(item, tuple) and isinstance(item[1], bytes):
                return item[1]
        return b""

    def _parse_message_bytes(self, raw_bytes: bytes) -> dict[str, Any] | None:
        message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        sender = parseaddr(message.get("From", ""))[1]
        if not sender:
            return None
        subject = self._decode_header_value(message.get("Subject", "")) or "(no subject)"
        body = self._extract_text_body(message).strip()
        if not body:
            body = subject
        body_clip = clip_text(
            body,
            limit=max(1, int(self.config.max_body_chars)),
            marker="email body preview truncated",
        )
        persisted = {}
        if body_clip.truncated:
            persisted = persist_tool_result(body, tool_name="email_body", extension="txt")
        thread_id = (
            str(message.get("Thread-Index", "") or "")
            or str(message.get("References", "") or "").split()[-1] if str(message.get("References", "") or "").split() else ""
        )
        if not thread_id:
            thread_id = str(message.get("Message-ID", "") or sender)
        return {
            "from": sender,
            "subject": subject,
            "body": body_clip.text,
            "body_truncated": body_clip.truncated,
            "body_omitted_chars": body_clip.omitted_chars,
            "full_body_path": persisted.get("full_output_path", ""),
            "message_id": str(message.get("Message-ID", "") or ""),
            "thread_id": thread_id,
        }

    @staticmethod
    def _decode_header_value(value: str) -> str:
        try:
            return str(make_header(decode_header(value)))
        except Exception:
            return value or ""

    def _extract_text_body(self, message: Any) -> str:
        if message.is_multipart():
            for part in message.walk():
                disposition = str(part.get_content_disposition() or "")
                content_type = str(part.get_content_type() or "")
                if disposition == "attachment":
                    continue
                if content_type == "text/plain":
                    payload = part.get_content()
                    return payload if isinstance(payload, str) else str(payload)
                if content_type == "text/html":
                    payload = part.get_content()
                    if isinstance(payload, str):
                        return self._html_to_text(payload)
            return ""
        payload = message.get_content()
        if isinstance(payload, str) and message.get_content_type() == "text/html":
            return self._html_to_text(payload)
        return payload if isinstance(payload, str) else str(payload)

    @staticmethod
    def _html_to_text(value: str) -> str:
        text = value.replace("<br>", "\n").replace("<br/>", "\n").replace("</p>", "\n")
        text = html.unescape(text)
        inside = False
        out: list[str] = []
        for char in text:
            if char == "<":
                inside = True
                continue
            if char == ">":
                inside = False
                continue
            if not inside:
                out.append(char)
        return "".join(out).strip()

    def _reply_subject(self, value: str) -> str:
        lowered = value.lower()
        prefix = self.config.subject_prefix or "Re: "
        return value if lowered.startswith(prefix.lower()) else f"{prefix}{value}"
