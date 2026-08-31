"""Tests for FileStore — sandbox-mode behavior verification.

These tests run with no env vars, no network, no DB. They are the
canary for the sandbox-mode contract from ADR-0001 — if these pass,
any tool that depends on Store via the registry will work in sandbox.
"""

from __future__ import annotations

import threading

import pytest

from runspace.protocols.store import FileStore, Store


@pytest.fixture
def store(tmp_path) -> FileStore:
    return FileStore(tmp_path)


# ── Protocol satisfaction ──────────────────────────────────────────────
def test_filestore_implements_store_protocol(store):
    assert isinstance(store, Store)


# ── list / get / save / update / delete / query ────────────────────────
def test_list_unknown_collection_returns_empty(store):
    assert store.list("missing") == []


def test_save_then_get(store):
    rec = store.save("invoices", {"id": "i01", "amount": 100, "currency": "EUR"})
    assert rec["amount"] == 100
    assert store.get("invoices", "i01") == rec


def test_save_requires_id(store):
    with pytest.raises(ValueError, match="must include an 'id'"):
        store.save("invoices", {"amount": 100})


def test_save_upserts_by_id(store):
    store.save("invoices", {"id": "i01", "amount": 100})
    store.save("invoices", {"id": "i01", "amount": 200})
    assert store.list("invoices") == [{"id": "i01", "amount": 200}]


def test_get_missing_returns_none(store):
    assert store.get("invoices", "missing") is None


def test_update_existing(store):
    store.save("invoices", {"id": "i01", "amount": 100, "status": "pending"})
    out = store.update("invoices", "i01", status="paid")
    assert out["status"] == "paid"
    assert out["amount"] == 100  # other fields preserved


def test_update_missing_returns_none(store):
    assert store.update("invoices", "missing", status="paid") is None


def test_delete_returns_true_when_existed(store):
    store.save("invoices", {"id": "i01"})
    assert store.delete("invoices", "i01") is True
    assert store.get("invoices", "i01") is None


def test_delete_returns_false_when_missing(store):
    assert store.delete("invoices", "missing") is False


def test_query_by_predicate(store):
    store.save("invoices", {"id": "i01", "status": "paid", "supplier": "X"})
    store.save("invoices", {"id": "i02", "status": "pending", "supplier": "X"})
    store.save("invoices", {"id": "i03", "status": "paid", "supplier": "Y"})
    out = store.query("invoices", status="paid")
    assert {r["id"] for r in out} == {"i01", "i03"}
    out = store.query("invoices", status="paid", supplier="X")
    assert [r["id"] for r in out] == ["i01"]


def test_query_no_predicate_lists_all(store):
    store.save("invoices", {"id": "i01"})
    store.save("invoices", {"id": "i02"})
    assert len(store.query("invoices")) == 2


# ── File semantics ─────────────────────────────────────────────────────
def test_atomic_write_no_tmp_files_left(store, tmp_path):
    store.save("invoices", {"id": "i01", "amount": 100})
    store.update("invoices", "i01", amount=200)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_corrupted_file_treated_as_empty(store, tmp_path):
    """A bad JSON file shouldn't crash — degrade to empty list."""
    (tmp_path / "invoices.json").write_text("not valid json {{{", encoding="utf-8")
    assert store.list("invoices") == []


def test_independent_collections(store):
    """Saving to one collection doesn't touch another."""
    store.save("invoices", {"id": "i01"})
    store.save("suppliers", {"id": "s01"})
    assert len(store.list("invoices")) == 1
    assert len(store.list("suppliers")) == 1


def test_per_collection_locks_dont_serialize_unrelated_writes(store):
    """Smoke: a thread saving to collection A should not block one
    saving to collection B. Serialization within a single collection
    is OK; cross-collection should be parallel."""
    barrier = threading.Barrier(2, timeout=2)

    def writer(coll, ident):
        barrier.wait()  # ensure both threads start ~together
        for i in range(50):
            store.save(coll, {"id": f"{ident}_{i}", "n": i})

    t1 = threading.Thread(target=writer, args=("a", "a"))
    t2 = threading.Thread(target=writer, args=("b", "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(store.list("a")) == 50
    assert len(store.list("b")) == 50


# ── Real-world shape (mirrors ADR-0001 «Ada invoice» canary) ─────────
def test_invoice_workflow(store):
    """Sanity check: typical Ada flow works through Store unchanged."""
    inv = store.save(
        "invoices",
        {
            "id": "i07",
            "supplier": "Cersanit",
            "amount": "230.84",
            "currency": "EUR",
            "due_date": "2026-05-10",
            "status": "pending",
        },
    )
    assert inv["status"] == "pending"
    # mark paid
    store.update("invoices", "i07", status="paid", paid_at="2026-05-09T10:00:00+00:00")
    assert store.get("invoices", "i07")["status"] == "paid"
    # query overdue
    assert store.query("invoices", status="paid")[0]["id"] == "i07"
