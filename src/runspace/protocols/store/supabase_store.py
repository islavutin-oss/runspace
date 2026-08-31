"""SupabaseStore — production Store backend."""

from __future__ import annotations

import os

from .protocol import Store


class SupabaseStore(Store):
    """Supabase-table-per-collection Store implementation.

    Lazy-imports `supabase-py` so the module can be loaded in environments
    that don't have it installed (e.g. sandbox-only test runs).
    """

    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = url or os.environ["SUPABASE_URL"]
        # Accept either env var name. The codebase historically used
        # SUPABASE_KEY (acme, initech), and the protocol layer originally
        # required SUPABASE_SERVICE_KEY. Both names refer to the same
        # service-role key — read whichever is set, prefer the explicit one.
        self.key = key or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
        if not self.key:
            raise RuntimeError("SupabaseStore needs SUPABASE_SERVICE_KEY or SUPABASE_KEY in env")
        self._client = None  # lazy

    @property
    def client(self):
        if self._client is None:
            from supabase import create_client  # type: ignore

            self._client = create_client(self.url, self.key)
        return self._client

    # ── Store ───────────────────────────────────────────────────────────
    def list(self, collection: str) -> list[dict]:
        return self.client.table(collection).select("*").execute().data or []

    def get(self, collection: str, id: str) -> dict | None:
        rows = (
            self.client.table(collection).select("*").eq("id", id).limit(1).execute().data
        ) or []
        return rows[0] if rows else None

    def save(self, collection: str, record: dict) -> dict:
        if "id" not in record:
            raise ValueError("record must include an 'id' field")
        # Try upsert first — preferred because save() is the
        # idempotent surface (callers may resubmit on retries).
        try:
            out = (
                self.client.table(collection).upsert(record, on_conflict="id").execute().data
            ) or [record]
            return out[0]
        except Exception as e:
            msg = str(e)
            if "42P10" not in msg and "ON CONFLICT" not in msg:
                raise
            # Fallback: try update; if no row, insert.
            existing = self.get(collection, record["id"])
            if existing:
                fields = {k: v for k, v in record.items() if k != "id"}
                out = (
                    self.client.table(collection)
                    .update(fields)
                    .eq("id", record["id"])
                    .execute()
                    .data
                ) or [record]
                return out[0]
            out = (self.client.table(collection).insert(record).execute().data) or [record]
            return out[0]

    def update(self, collection: str, id: str, **fields) -> dict | None:
        if not fields:
            return self.get(collection, id)
        out = (self.client.table(collection).update(fields).eq("id", id).execute().data) or []
        return out[0] if out else None

    def delete(self, collection: str, id: str) -> bool:
        out = (self.client.table(collection).delete().eq("id", id).execute().data) or []
        return bool(out)

    def query(self, collection: str, **predicate) -> list[dict]:
        q = self.client.table(collection).select("*")
        for k, v in predicate.items():
            q = q.eq(k, v)
        return q.execute().data or []
