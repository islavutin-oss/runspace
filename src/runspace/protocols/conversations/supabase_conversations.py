"""SupabaseConversations — a Supabase-backed Conversations adapter."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .models import Message, Thread, thread_key
from .protocol import Conversations

_VALID = ("a", "b", "system")
_COLS = "id,tenant,thread_key,party_a,party_b,sender,sender_name,body,meta,read_at,created_at"


class SupabaseConversations(Conversations):
    """`Conversations` over a single Supabase table.

    The table name is configurable (default `conversations_messages`)
    so several products can share one project or keep their own. Pass
    an existing `client=` to reuse a connection; otherwise the adapter
    builds one from SUPABASE_URL + SUPABASE_SERVICE_KEY / SUPABASE_KEY.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        key: str | None = None,
        table: str = "conversations_messages",
        client=None,
    ) -> None:
        self.table = table
        self._client = client
        if client is None:
            self.url = url or os.environ.get("SUPABASE_URL", "")
            self.key = (
                key or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")
            )
            if not (self.url and self.key):
                raise RuntimeError(
                    "SupabaseConversations needs SUPABASE_URL + "
                    "SUPABASE_SERVICE_KEY in env, or an explicit client="
                )

    @property
    def client(self):
        if self._client is None:
            from supabase import create_client

            self._client = create_client(self.url, self.key)
        return self._client

    def _tbl(self):
        return self.client.table(self.table)

    @staticmethod
    def _to_message(r: dict) -> Message:
        return Message(
            id=r.get("id"),
            tenant=r.get("tenant") or "",
            thread_key=r["thread_key"],
            party_a=r["party_a"],
            party_b=r["party_b"],
            sender=r["sender"],
            sender_name=r.get("sender_name"),
            body=r["body"],
            meta=r.get("meta"),
            read_at=r.get("read_at"),
            created_at=r.get("created_at"),
        )

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
        row = {
            "tenant": tenant,
            "thread_key": thread_key(tenant, party_a, party_b),
            "party_a": party_a,
            "party_b": party_b,
            "sender": sender,
            "sender_name": sender_name,
            "body": body,
            "meta": meta,
        }
        res = self._tbl().insert(row).execute()
        return self._to_message(res.data[0])

    def thread(
        self,
        *,
        tenant: str,
        party_a: str,
        party_b: str,
        limit: int = 200,
    ) -> list[Message]:
        key = thread_key(tenant, party_a, party_b)
        res = (
            self._tbl()
            .select(_COLS)
            .eq("thread_key", key)
            .order("created_at", desc=False)
            .order("id", desc=False)
            .limit(limit)
            .execute()
        )
        return [self._to_message(r) for r in (res.data or [])]

    def list_threads(self, *, tenant: str, party: str, role: str) -> list[Thread]:
        if role not in ("a", "b"):
            raise ValueError("role must be 'a' or 'b'")
        col = "party_a" if role == "a" else "party_b"
        res = (
            self._tbl()
            .select(_COLS)
            .eq("tenant", tenant)
            .eq(col, party)
            .order("created_at", desc=False)
            .execute()
        )
        grouped: dict[str, list[Message]] = {}
        for r in res.data or []:
            m = self._to_message(r)
            grouped.setdefault(m.thread_key, []).append(m)

        out: list[Thread] = []
        for msgs in grouped.values():
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
        out.sort(key=lambda t: str(t.last_at or ""), reverse=True)
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
        now = datetime.now(timezone.utc).isoformat()
        # Mark every still-unread message the reader did not send.
        res = (
            self._tbl()
            .update({"read_at": now})
            .eq("thread_key", key)
            .is_("read_at", "null")
            .neq("sender", reader_role)
            .execute()
        )
        return len(res.data or [])
