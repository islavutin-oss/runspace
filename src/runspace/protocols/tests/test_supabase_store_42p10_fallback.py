"""Regression: SupabaseStore.save() must survive PostgREST schema-cache lag (error code 42P10)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from runspace.protocols.store.supabase_store import SupabaseStore


class _Err(Exception):
    """Stand-in for postgrest.exceptions.APIError without importing it."""

    pass


def _make_42p10_error():
    return _Err(
        "{'message': 'there is no unique or exclusion constraint "
        "matching the ON CONFLICT specification', 'code': '42P10', "
        "'hint': None, 'details': None}"
    )


@pytest.fixture
def store():
    """Build a SupabaseStore with a mocked client so we can simulate
    the schema-cache failure without a real Supabase project.
    `client` is a @property that lazy-creates from `_client`; we
    pre-fill `_client` so the property short-circuits."""
    s = SupabaseStore.__new__(SupabaseStore)
    s.url = "http://fake"
    s.key = "fake-key"
    s._client = MagicMock()
    return s


def test_upsert_succeeds_path(store):
    """Happy path — when PostgREST is healthy, save uses upsert and
    returns the upserted row. No fallback should trigger."""
    record = {"id": "i01", "supplier": "X", "amount_cents": 1, "currency": "EUR"}
    store._client.table.return_value.upsert.return_value.execute.return_value.data = [record]

    out = store.save("invoices", record)
    assert out == record
    # Update + insert paths must NOT have been called
    store._client.table.return_value.update.assert_not_called()
    store._client.table.return_value.insert.assert_not_called()


def test_42p10_falls_back_to_insert_for_new_row(store):
    """Cache-lag bug path: upsert raises 42P10. With no existing
    row, fallback should INSERT."""
    record = {"id": "i02", "supplier": "Y", "amount_cents": 1, "currency": "EUR"}
    # upsert raises
    store._client.table.return_value.upsert.return_value.execute.side_effect = _make_42p10_error()
    # get returns no row
    store._client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    # insert succeeds
    store._client.table.return_value.insert.return_value.execute.return_value.data = [record]

    out = store.save("invoices", record)
    assert out == record
    store._client.table.return_value.insert.assert_called_once()


def test_42p10_falls_back_to_update_when_row_exists(store):
    """If a row with the same id already exists, fallback should
    UPDATE (matching upsert's last-write-wins semantics) — not insert
    (which would race against the unique constraint)."""
    record = {"id": "i03", "supplier": "Z (new)", "amount_cents": 5, "currency": "EUR"}
    existing = {"id": "i03", "supplier": "Z (old)", "amount_cents": 1, "currency": "EUR"}
    store._client.table.return_value.upsert.return_value.execute.side_effect = _make_42p10_error()
    store._client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        existing
    ]
    store._client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
        record
    ]

    out = store.save("invoices", record)
    assert out == record
    # Update was used — not insert (race-safe choice)
    store._client.table.return_value.update.assert_called_once()
    store._client.table.return_value.insert.assert_not_called()


def test_non_42p10_errors_still_propagate(store):
    """Only the specific schema-cache error gets the fallback. Real
    errors (RLS, validation, network) must surface normally so callers
    can react."""
    record = {"id": "i04", "supplier": "X", "amount_cents": 1, "currency": "EUR"}
    store._client.table.return_value.upsert.return_value.execute.side_effect = RuntimeError(
        "some other error"
    )
    with pytest.raises(RuntimeError, match="some other error"):
        store.save("invoices", record)


def test_missing_id_still_rejected(store):
    """The pre-existing contract (id required) must not be relaxed
    by the new fallback branch."""
    with pytest.raises(ValueError, match="must include an 'id' field"):
        store.save("invoices", {"supplier": "X", "amount_cents": 1, "currency": "EUR"})
