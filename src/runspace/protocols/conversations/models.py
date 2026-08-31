"""Conversation data types — two-party on-platform messaging."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

#: Who sent a message. `system` is the platform itself (e.g. an order
#: confirmation dropped into the thread by a bot).
Sender = Literal["a", "b", "system"]


def thread_key(tenant: str, party_a: str, party_b: str) -> str:
    """Deterministic key for the (tenant, party_a, party_b) thread.

    Roles are fixed — `party_a` and `party_b` are not interchangeable,
    so both sides of the conversation resolve to the same key."""
    return f"{tenant}|{party_a}|{party_b}"


class Message(BaseModel):
    """One message in a thread."""

    id: int | None = None
    tenant: str = ""
    thread_key: str
    party_a: str
    party_b: str
    sender: Sender
    sender_name: str | None = None
    body: str
    meta: dict | None = None
    #: When the recipient saw it (None = unread). One timestamp per
    #: message — adequate for two-party threads; per-party read state
    #: would need a separate reads table (a future revision).
    read_at: datetime | None = None
    created_at: datetime | None = None


class Thread(BaseModel):
    """A two-party conversation, summarised for an inbox list."""

    tenant: str = ""
    thread_key: str
    party_a: str
    party_b: str
    last_body: str | None = None
    last_sender: Sender | None = None
    last_at: datetime | None = None
    message_count: int = 0
    #: Messages the viewer has not read (not sent by them, `read_at` None).
    unread: int = 0
