"""DocumentStore — thin wrapper over `protocols.Store("documents")`."""

from __future__ import annotations

COLLECTION = "documents"


class DocumentStore:
    """Per-tenant view over the documents collection."""

    def __init__(self, store, tenant_id: str):
        self._store = store
        self._tenant_id = tenant_id

    # ── reads ───────────────────────────────────────────────────────────
    def get_by_id(self, doc_id: str) -> dict | None:
        """Return one document row or None. Tenant-isolated."""
        rec = self._store.get(COLLECTION, doc_id)
        if not rec or rec.get("tenant_id") != self._tenant_id:
            return None
        return rec

    def get_by_storage_path(self, storage_path: str) -> dict | None:
        rows = self._store.query(COLLECTION, tenant_id=self._tenant_id, storage_path=storage_path)
        return rows[0] if rows else None

    def get_by_filename(self, filename: str) -> dict | None:
        rows = self._store.query(COLLECTION, tenant_id=self._tenant_id, filename=filename)
        return rows[0] if rows else None

    def list(
        self,
        source: str | None = None,
        agent_id: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List documents with optional filters. q matches filename
        / title / extracted_text via case-insensitive substring."""
        rows = self._store.query(COLLECTION, tenant_id=self._tenant_id)
        rows = [r for r in rows if r.get("deleted_at") is None]
        if source:
            rows = [r for r in rows if r.get("source") == source]
        if agent_id:
            rows = [r for r in rows if r.get("agent_id") == agent_id]
        if q:
            needle = q.lower()
            rows = [
                r
                for r in rows
                if any(
                    needle in (r.get(field) or "").lower()
                    for field in ("filename", "title", "extracted_text")
                )
            ]
        # Newest first.
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return rows[offset : offset + limit]

    # ── writes ──────────────────────────────────────────────────────────
    def insert(self, row: dict) -> dict:
        """Insert a document row. Forces tenant_id and ensures id is set."""
        rec = dict(row)
        rec["tenant_id"] = self._tenant_id
        if not rec.get("id"):
            import uuid

            rec["id"] = f"doc_{uuid.uuid4().hex[:12]}"
        self._store.save(COLLECTION, rec)
        return rec

    def update(self, doc_id: str, **fields) -> dict | None:
        rec = self.get_by_id(doc_id)
        if not rec:
            return None
        rec.update(fields)
        self._store.save(COLLECTION, rec)
        return rec

    def soft_delete(self, doc_id: str) -> bool:
        from datetime import datetime

        rec = self.get_by_id(doc_id)
        if not rec:
            return False
        rec["deleted_at"] = datetime.now().isoformat()
        self._store.save(COLLECTION, rec)
        return True


def get_document_store(tenant_id: str) -> DocumentStore:
    """Factory matching the InvoiceStore / BookingStore / CustomerStore
    pattern."""
    from runspace.protocols import get_store

    return DocumentStore(get_store(), tenant_id)
