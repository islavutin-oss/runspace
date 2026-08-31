"""Transport protocol — inbound message + file ingestion stream."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class Attachment:
    file_id: str
    filename: str
    mime: str | None = None
    size: int | None = None


@dataclass
class IncomingMessage:
    """One message arriving on a transport."""

    transport: str
    """Transport id, e.g. 'telegram', 'file-inbox'."""
    chat_id: str
    """Stable id of the channel/chat the message came from."""
    sender: str
    """Display name of the sender (forwarded-by, or actual sender)."""
    sender_role: str
    """Role label, e.g. 'operator', 'supplier', 'system'."""
    text: str
    """Caption / body of the message; may be empty if attachment-only."""
    attachments: list[Attachment] = field(default_factory=list)
    msg_id: str = ""
    """Stable per-transport message id (for dedupe / threading)."""
    ts: str = ""
    """ISO timestamp from the transport (or local now() if absent)."""


MessageCallback = Callable[[IncomingMessage], Awaitable[None]]


@runtime_checkable
class Transport(Protocol):
    """Inbound message + file ingestion."""

    name: str  # 'telegram', 'file-inbox', etc.

    async def start(self) -> None:
        """Begin listening (open webhook / start polling / watch dir)."""
        ...

    async def stop(self) -> None:
        """Stop listening cleanly."""
        ...

    def on_message(self, cb: MessageCallback) -> None:
        """Register a callback that fires for each incoming message."""
        ...

    async def fetch_file(self, file_id: str) -> bytes:
        """Download an attachment by its file_id."""
        ...
