"""Conversations protocol — two-party on-platform messaging."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import Message, Thread


@runtime_checkable
class Conversations(Protocol):
    """Two-party threaded messaging.

    A thread is identified by (tenant, party_a, party_b) with fixed
    roles. Implementations must:
      - Treat an unknown thread as empty (`thread` returns []), not raise.
      - Order `thread` oldest-first; `list_threads` most-recent-first.
      - Be safe for concurrent reads and serialise concurrent writes.
      - Accept only 'a' / 'b' / 'system' as `sender`.
    """

    def post(
        self,
        *,
        tenant: str,
        party_a: str,
        party_b: str,
        sender: str,
        body: str,
        sender_name: str | None = None,
        meta: dict | None = None,
    ) -> Message:
        """Append a message to the (party_a, party_b) thread. `sender` is
        'a', 'b' or 'system'. Returns the stored message."""
        ...

    def thread(
        self,
        *,
        tenant: str,
        party_a: str,
        party_b: str,
        limit: int = 200,
    ) -> list[Message]:
        """Messages in one thread, oldest-first (most recent `limit`)."""
        ...

    def list_threads(self, *, tenant: str, party: str, role: str) -> list[Thread]:
        """Every thread `party` takes part in as the given `role`
        ('a' or 'b'), most-recent-first — an inbox list."""
        ...

    def mark_read(
        self,
        *,
        tenant: str,
        party_a: str,
        party_b: str,
        reader_role: str,
    ) -> int:
        """Mark messages the reader did NOT send as read. `reader_role`
        is 'a' or 'b'. Returns how many messages were marked."""
        ...
