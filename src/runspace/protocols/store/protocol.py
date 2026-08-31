"""Store protocol — generic CRUD over named collections."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Store(Protocol):
    """Generic CRUD over named collections.

    Records are dicts with at minimum an `id: str` field. Implementations
    must:
      - Be safe for concurrent reads.
      - Serialize concurrent writes (per-collection lock or DB transaction).
      - Treat unknown collections as empty (`list` returns []), not raise.
      - Be idempotent on `save` (upsert by id).

    Implementations MAY:
      - Add observability (logging, metrics) inside the methods.
      - Cache reads — but invalidation is the impl's problem.
    """

    def list(self, collection: str) -> list[dict]:
        """All records in `collection`. Empty list if collection unknown."""
        ...

    def get(self, collection: str, id: str) -> dict | None:
        """One record by id. None if not found."""
        ...

    def save(self, collection: str, record: dict) -> dict:
        """Upsert by id. record MUST contain an `id` key. Returns the
        stored record (including any timestamps the impl injects)."""
        ...

    def update(self, collection: str, id: str, **fields) -> dict | None:
        """Patch named fields on an existing record. None if not found.
        Equivalent to `get` → mutate → `save`, but atomic per impl."""
        ...

    def delete(self, collection: str, id: str) -> bool:
        """Remove the record. True if it existed, False otherwise."""
        ...

    def query(self, collection: str, **predicate) -> list[dict]:
        """Filter records by exact-match key/value predicate. Equivalent
        to [r for r in list(coll) if all(r.get(k) == v for k,v in pred.items())].
        Impls with a real query layer (Supabase) should push this down."""
        ...
