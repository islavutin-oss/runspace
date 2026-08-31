"""Live-vs-sandbox contract tests — every Store impl must behave the same."""

from __future__ import annotations

import threading

import pytest

from runspace.protocols.store import FileStore, InMemoryStore, Store


# ── Parametrize over every sandbox-mode Store impl ─────────────────────
@pytest.fixture(
    params=[
        pytest.param("file", id="FileStore"),
        pytest.param("memory", id="InMemoryStore"),
    ]
)
def store(request, tmp_path) -> Store:
    if request.param == "file":
        return FileStore(tmp_path)
    if request.param == "memory":
        return InMemoryStore()
    raise ValueError(f"unknown store kind: {request.param}")


# ── Read semantics ──────────────────────────────────────────────────────
class TestRead:
    def test_unknown_collection_returns_empty_list(self, store):
        """Per protocol: unknown collection → []. Never raise."""
        assert store.list("never_used") == []

    def test_get_missing_id_returns_none(self, store):
        assert store.get("invoices", "missing") is None

    def test_query_unknown_collection_returns_empty(self, store):
        assert store.query("never_used", status="paid") == []

    def test_query_no_predicate_lists_all(self, store):
        store.save("invoices", {"id": "i01"})
        store.save("invoices", {"id": "i02"})
        assert len(store.query("invoices")) == 2


# ── Write semantics ────────────────────────────────────────────────────
class TestWrite:
    def test_save_then_read(self, store):
        store.save("invoices", {"id": "i01", "amount": 100})
        assert store.get("invoices", "i01") == {"id": "i01", "amount": 100}

    def test_save_requires_id(self, store):
        with pytest.raises(ValueError, match="must include an 'id'"):
            store.save("invoices", {"amount": 100})

    def test_save_upserts_by_id(self, store):
        """Same id, second save → replace, not append."""
        store.save("invoices", {"id": "i01", "amount": 100})
        store.save("invoices", {"id": "i01", "amount": 200})
        rows = store.list("invoices")
        assert len(rows) == 1
        assert rows[0]["amount"] == 200

    def test_update_existing(self, store):
        store.save("invoices", {"id": "i01", "amount": 100, "status": "pending"})
        out = store.update("invoices", "i01", status="paid")
        assert out["status"] == "paid"
        assert out["amount"] == 100

    def test_update_missing_returns_none(self, store):
        assert store.update("invoices", "missing", status="paid") is None

    def test_update_does_not_mutate_caller_dict(self, store):
        """The argument dict shouldn't be modified — store owns its copy."""
        record = {"id": "i01", "amount": 100}
        store.save("invoices", record)
        store.update("invoices", "i01", amount=200)
        assert record["amount"] == 100  # caller's dict untouched


# ── Delete semantics ───────────────────────────────────────────────────
class TestDelete:
    def test_delete_existing_returns_true(self, store):
        store.save("invoices", {"id": "i01"})
        assert store.delete("invoices", "i01") is True
        assert store.get("invoices", "i01") is None

    def test_delete_missing_returns_false(self, store):
        assert store.delete("invoices", "missing") is False

    def test_delete_one_does_not_affect_others(self, store):
        store.save("invoices", {"id": "i01"})
        store.save("invoices", {"id": "i02"})
        store.delete("invoices", "i01")
        assert store.get("invoices", "i02") is not None


# ── Query semantics ────────────────────────────────────────────────────
class TestQuery:
    def test_query_single_predicate(self, store):
        store.save("invoices", {"id": "i01", "status": "paid"})
        store.save("invoices", {"id": "i02", "status": "pending"})
        out = store.query("invoices", status="paid")
        assert {r["id"] for r in out} == {"i01"}

    def test_query_multiple_predicates_intersect(self, store):
        store.save("invoices", {"id": "i01", "status": "paid", "supplier": "X"})
        store.save("invoices", {"id": "i02", "status": "paid", "supplier": "Y"})
        store.save("invoices", {"id": "i03", "status": "pending", "supplier": "X"})
        out = store.query("invoices", status="paid", supplier="X")
        assert [r["id"] for r in out] == ["i01"]

    def test_query_no_match_returns_empty(self, store):
        store.save("invoices", {"id": "i01", "status": "paid"})
        assert store.query("invoices", status="never") == []


# ── Collection isolation ───────────────────────────────────────────────
class TestCollectionIsolation:
    def test_save_to_one_does_not_affect_another(self, store):
        store.save("invoices", {"id": "i01"})
        store.save("suppliers", {"id": "s01"})
        assert len(store.list("invoices")) == 1
        assert len(store.list("suppliers")) == 1

    def test_same_id_in_different_collections_kept_independent(self, store):
        """An id is unique within a collection, not globally."""
        store.save("invoices", {"id": "shared", "kind": "invoice"})
        store.save("suppliers", {"id": "shared", "kind": "supplier"})
        assert store.get("invoices", "shared")["kind"] == "invoice"
        assert store.get("suppliers", "shared")["kind"] == "supplier"

    def test_delete_one_collection_does_not_affect_another(self, store):
        store.save("invoices", {"id": "i01"})
        store.save("suppliers", {"id": "s01"})
        store.delete("invoices", "i01")
        assert store.list("invoices") == []
        assert len(store.list("suppliers")) == 1


# ── Result-purity (mutation isolation) ────────────────────────────────
class TestResultPurity:
    def test_list_result_safe_to_mutate(self, store):
        """If a caller mutates the list returned, the store's data
        shouldn't change."""
        store.save("invoices", {"id": "i01", "items": [1, 2]})
        out = store.list("invoices")
        out[0]["items"].append(99)  # caller mutates
        # Store unchanged
        fresh = store.list("invoices")
        assert fresh[0]["items"] == [1, 2]

    def test_get_result_safe_to_mutate(self, store):
        store.save("invoices", {"id": "i01", "items": [1, 2]})
        rec = store.get("invoices", "i01")
        rec["items"].append(99)
        fresh = store.get("invoices", "i01")
        assert fresh["items"] == [1, 2]


# ── Concurrency ────────────────────────────────────────────────────────
class TestConcurrency:
    def test_concurrent_writes_to_different_collections_dont_serialize_too_much(self, store):
        """Per-collection locking — A and B should overlap, not block each
        other entirely. Smoke check: both threads finish their work."""
        barrier = threading.Barrier(2, timeout=2)

        def writer(coll, ident):
            barrier.wait()
            for i in range(20):
                store.save(coll, {"id": f"{ident}_{i}", "n": i})

        t1 = threading.Thread(target=writer, args=("a", "a"))
        t2 = threading.Thread(target=writer, args=("b", "b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert len(store.list("a")) == 20
        assert len(store.list("b")) == 20

    def test_concurrent_writes_to_same_collection_dont_lose_records(self, store):
        """Per-collection serialization — N writers, no lost updates."""
        N = 50
        threads = [
            threading.Thread(target=lambda i=i: store.save("invoices", {"id": f"i{i:03d}", "n": i}))
            for i in range(N)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(store.list("invoices")) == N


# ── End-to-end happy-path (mirror real Ada invoice flow) ─────────────
def test_invoice_lifecycle_end_to_end(store):
    """A full invoice journey: created → marked paid → queried as paid.
    This is the canonical workflow that every Store impl must support."""
    inv = store.save(
        "invoices",
        {
            "id": "i07",
            "supplier": "Cersanit",
            "amount_cents": 23084,
            "currency": "EUR",
            "due_date": "2026-05-10",
            "status": "pending",
        },
    )
    assert inv["status"] == "pending"

    # confirm we can query for unpaid before paying
    pending = store.query("invoices", status="pending")
    assert any(r["id"] == "i07" for r in pending)

    # mark paid
    store.update("invoices", "i07", status="paid", paid_at="2026-05-09T10:00:00+00:00")
    assert store.get("invoices", "i07")["status"] == "paid"

    # filter changes appropriately
    assert store.query("invoices", status="pending") == []
    paid = store.query("invoices", status="paid")
    assert [r["id"] for r in paid] == ["i07"]

    # historical record persists across reads (no cache-staleness drift)
    again = store.get("invoices", "i07")
    assert again["paid_at"] == "2026-05-09T10:00:00+00:00"
