"""FileInboxTransport — sandbox Transport impl."""

from __future__ import annotations

import json
from pathlib import Path

from .protocol import Attachment, IncomingMessage, MessageCallback, Transport


class FileInboxTransport(Transport):
    """Filesystem-backed sandbox transport. Replay-once on start()."""

    name = "file-inbox"

    def __init__(self, inbox_dir: Path | str):
        self.inbox_dir = Path(inbox_dir)
        self._callbacks: list[MessageCallback] = []
        self._running = False

    def on_message(self, cb: MessageCallback) -> None:
        self._callbacks.append(cb)

    async def start(self) -> None:
        self._running = True
        if not self.inbox_dir.exists():
            return
        for envelope in sorted(self.inbox_dir.glob("*.json")):
            try:
                data = json.loads(envelope.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            msg = IncomingMessage(
                transport=self.name,
                chat_id=data.get("chat_id", "default"),
                sender=data.get("sender", "unknown"),
                sender_role=data.get("sender_role", "operator"),
                text=data.get("text", ""),
                attachments=[
                    Attachment(
                        file_id=a["file_id"],
                        filename=a.get("filename", a["file_id"]),
                        mime=a.get("mime"),
                        size=a.get("size"),
                    )
                    for a in data.get("attachments", [])
                ],
                msg_id=envelope.stem,
                ts=data.get("ts", ""),
            )
            for cb in self._callbacks:
                await cb(msg)
        # One-shot — sandbox doesn't keep listening. Real transports do.

    async def stop(self) -> None:
        self._running = False

    async def fetch_file(self, file_id: str) -> bytes:
        p = self.inbox_dir / file_id
        if not p.exists():
            raise FileNotFoundError(f"file_id {file_id!r} not in {self.inbox_dir}")
        return p.read_bytes()
