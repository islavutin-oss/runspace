"""TelegramTransport — production Transport impl via Telegram Bot API."""

from __future__ import annotations

import os

import httpx

from .protocol import Attachment, IncomingMessage, MessageCallback, Transport

TG_API = "https://api.telegram.org"


class TelegramTransport(Transport):
    """Telegram Bot API transport. Webhook-driven; no internal polling.

    Mount `webhook_handler(payload)` from your FastAPI route handler.
    """

    name = "telegram"

    def __init__(self, *, token: str | None = None, timeout: float = 30):
        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        self.timeout = timeout
        self._callbacks: list[MessageCallback] = []
        self._client = httpx.AsyncClient(timeout=timeout)

    def on_message(self, cb: MessageCallback) -> None:
        self._callbacks.append(cb)

    async def start(self) -> None:
        # Webhook-driven; no startup task needed.
        pass

    async def stop(self) -> None:
        await self._client.aclose()

    async def webhook_handler(self, payload: dict) -> None:
        """Convert one Telegram Update payload → IncomingMessage and fan out."""
        msg = self._parse_update(payload)
        if msg is None:
            return
        for cb in self._callbacks:
            await cb(msg)

    async def fetch_file(self, file_id: str) -> bytes:
        info = await self._client.get(
            f"{TG_API}/bot{self.token}/getFile",
            params={"file_id": file_id},
        )
        info.raise_for_status()
        file_path = info.json()["result"]["file_path"]
        dl = await self._client.get(f"{TG_API}/file/bot{self.token}/{file_path}")
        dl.raise_for_status()
        return dl.content

    # ── helpers ─────────────────────────────────────────────────────────
    def _parse_update(self, update: dict) -> IncomingMessage | None:
        """Extract an IncomingMessage from a Telegram Update.

        Handles regular messages, forwarded messages, and document/photo
        attachments. Returns None for updates we don't care about
        (edited_message, callback_query, etc.)."""
        m = update.get("message")
        if not m:
            return None
        chat_id = str(m.get("chat", {}).get("id", ""))
        sender_block = m.get("forward_from") or m.get("from") or {}
        sender = (
            sender_block.get("first_name", "") + " " + sender_block.get("last_name", "")
        ).strip() or sender_block.get("username", "unknown")
        sender_role = "operator" if m.get("forward_from") else "user"
        text = m.get("caption") or m.get("text") or ""

        attachments: list[Attachment] = []
        if "document" in m:
            d = m["document"]
            attachments.append(
                Attachment(
                    file_id=d["file_id"],
                    filename=d.get("file_name", d["file_id"]),
                    mime=d.get("mime_type"),
                    size=d.get("file_size"),
                )
            )
        if "photo" in m and m["photo"]:
            largest = max(m["photo"], key=lambda p: p.get("file_size", 0))
            attachments.append(
                Attachment(
                    file_id=largest["file_id"],
                    filename=f"photo_{largest['file_id']}.jpg",
                    mime="image/jpeg",
                    size=largest.get("file_size"),
                )
            )

        return IncomingMessage(
            transport=self.name,
            chat_id=chat_id,
            sender=sender,
            sender_role=sender_role,
            text=text,
            attachments=attachments,
            msg_id=str(m.get("message_id", "")),
            ts=str(m.get("date", "")),
        )
