"""InMemoryConversations — process-local Conversations backend.

Pure list-backed. For parametrised contract tests and sandbox runs;
not for data that must survive a restart or for multi-process use.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from .models import Message, Thread, thread_key
from .protocol import Conversations

_VALID = ("a", "b", "system")


class InMemoryConversations(Conversations):
    """List-backed `Conversations` impl."""

    def __init__(self) -> None:
        self._msgs: list[Message] = []
        self._lock = threading.Lock()
        self._seq = 0

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
        if sender not in _VALID:
            raise ValueError(f"sender must be one of {_VALID}, got {sender!r}")
        if not body.strip():
            raise ValueError("message body is empty")
        with self._lock:
            self._seq += 1
            msg = Message(
                id=self._seq,
                tenant=tenant,
                thread_key=thread_key(tenant, party_a, party_b),
                party_a=party_a,
                party_b=party_b,
                sender=sender,  # type: ignore[arg-type]
                sender_name=sender_name,
                body=body,
                meta=meta,
                created_at=datetime.now(timezone.utc),
            )
            self._msgs.append(msg)
        return msg.model_copy(deep=True)

    def thread(
        self,
        *,
        tenant: str,
        party_a: str,
        party_b: str,
        limit: int = 200,
    ) -> list[Message]:
        key = thread_key(tenant, party_a, party_b)
        rows = sorted(
            (m for m in self._msgs if m.thread_key == key),
            key=lambda m: m.id or 0,
        )
        return [m.model_copy(deep=True) for m in rows[-limit:]]

    def list_threads(self, *, tenant: str, party: str, role: str) -> list[Thread]:
        if role not in ("a", "b"):
            raise ValueError("role must be 'a' or 'b'")
        field = "party_a" if role == "a" else "party_b"
        grouped: dict[str, list[Message]] = {}
        for m in self._msgs:
            if m.tenant == tenant and getattr(m, field) == party:
                grouped.setdefault(m.thread_key, []).append(m)

        out: list[Thread] = []
        for msgs in grouped.values():
            msgs.sort(key=lambda m: m.id or 0)
            last = msgs[-1]
            unread = sum(1 for m in msgs if m.read_at is None and m.sender != role)
            out.append(
                Thread(
                    tenant=tenant,
                    thread_key=last.thread_key,
                    party_a=last.party_a,
                    party_b=last.party_b,
                    last_body=last.body,
                    last_sender=last.sender,
                    last_at=last.created_at,
                    message_count=len(msgs),
                    unread=unread,
                )
            )
        out.sort(key=lambda t: t.last_at or datetime.min, reverse=True)
        return out

    def mark_read(
        self,
        *,
        tenant: str,
        party_a: str,
        party_b: str,
        reader_role: str,
    ) -> int:
        if reader_role not in ("a", "b"):
            raise ValueError("reader_role must be 'a' or 'b'")
        key = thread_key(tenant, party_a, party_b)
        now = datetime.now(timezone.utc)
        marked = 0
        with self._lock:
            for m in self._msgs:
                if m.thread_key == key and m.read_at is None and m.sender != reader_role:
                    m.read_at = now
                    marked += 1
        return marked
