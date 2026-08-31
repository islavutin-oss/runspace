"""Multi-tenant isolation property tests — security hardening."""

from __future__ import annotations

import itertools
import string

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from runspace.protocols.store import FileStore, InMemoryStore, Store

# Hypothesis settings: sandbox tests should be fast and deterministic. We
# The function-scoped-fixture health check is suppressed because the
# fixture hands back a factory rather than shared state; every example
# builds its own store.
_settings = settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ── Hypothesis strategies ──────────────────────────────────────────────
# Tenant ids: include adversarial cases (paths, dots, slashes, unicode).
_safe_chars = string.ascii_letters + string.digits + "-_"
_tenant_ids = st.text(
    alphabet=_safe_chars + "/.\\:",  # include path-ish characters
    min_size=1,
    max_size=20,
)

# Record ids: similar charset, never empty.
_record_ids = st.text(
    alphabet=string.ascii_letters + string.digits + "-_",
    min_size=1,
    max_size=10,
)

# Record bodies: small dicts with arbitrary string-keyed values.
_record_values = st.recursive(
    st.one_of(st.integers(), st.text(min_size=0, max_size=20), st.booleans(), st.none()),
    lambda children: st.lists(children, max_size=3),
    max_leaves=5,
)
_record_bodies = st.dictionaries(
    keys=st.text(alphabet=string.ascii_letters, min_size=1, max_size=10),
    values=_record_values,
    max_size=5,
)


# ── Parametrize over every Store impl ──────────────────────────────────
@pytest.fixture(
    params=[
        pytest.param("file", id="FileStore"),
        pytest.param("memory", id="InMemoryStore"),
    ]
)
def make_store(request, tmp_path):
    """Build a *fresh* store per call.

    @given runs many examples against one function-scoped fixture, so a
    single shared store accumulates rows across examples and a tenant id
    generated as `tenant_a` in one example can reappear as `tenant_b` in a
    later one — which looks exactly like a leak. Each example gets its own
    store instead.
    """
    counter = itertools.count()

    def _make() -> Store:
        if request.param == "file":
            path = tmp_path / f"store_{next(counter)}"
            path.mkdir()
            return FileStore(path)
        return InMemoryStore()

    return _make


# ── P1: no leakage between tenants ──────────────────────────────────────
class TestTenantQueryIsolation:
    @given(
        tenant_a=_tenant_ids,
        tenant_b=_tenant_ids,
        record_id=_record_ids,
        body=_record_bodies,
    )
    @_settings
    def test_query_b_never_sees_a(self, make_store, tenant_a, tenant_b, record_id, body):
        """For any tenant pair (A, B) with B != A: data saved under A is
        never returned by query(tenant_id=B)."""
        store = make_store()
        assume(tenant_a != tenant_b)
        # Reset storage between hypothesis examples by using a unique
        # collection name (tmp_path is reused within one test run).
        coll = f"test_{record_id}"
        record = {"id": record_id, "tenant_id": tenant_a, **body}
        store.save(coll, record)
        result = store.query(coll, tenant_id=tenant_b)
        assert result == [], f"leakage: tenant {tenant_b!r} saw tenant {tenant_a!r}'s record"


# ── P2: collection isolation ───────────────────────────────────────────
class TestCollectionIsolation:
    @given(
        suffix_x=st.text(alphabet=string.ascii_letters, min_size=1, max_size=4),
        suffix_y=st.text(alphabet=string.ascii_letters, min_size=1, max_size=4),
        record_id=_record_ids,
        body_x=_record_bodies,
        body_y=_record_bodies,
    )
    @_settings
    def test_collections_dont_alias(
        self, make_store, suffix_x, suffix_y, record_id, body_x, body_y
    ):
        """Same record_id in collections X and Y must remain independent.
        Reading X never returns Y's records, and vice versa.

        Note: collection names must be unique per hypothesis example,
        otherwise the function-scoped `store` fixture accumulates records
        across examples. The unique-pair (suffix_x, suffix_y, record_id)
        gives each example its own pair of collections."""
        store = make_store()
        assume(suffix_x != suffix_y)
        coll_x = f"x_{suffix_x}_{record_id}"
        coll_y = f"y_{suffix_y}_{record_id}"
        store.save(coll_x, {"id": record_id, **body_x})
        store.save(coll_y, {"id": record_id, **body_y})
        x_rows = store.list(coll_x)
        y_rows = store.list(coll_y)
        assert len(x_rows) == 1
        assert len(y_rows) == 1
        for k, v in body_x.items():
            assert x_rows[0].get(k) == v, f"collection {coll_x!r} lost field {k!r}"
        for k, v in body_y.items():
            assert y_rows[0].get(k) == v, f"collection {coll_y!r} lost field {k!r}"


# ── P3: special-character safety in tenant ids ────────────────────────
class TestSpecialCharSafety:
    """Adversarial tenant ids — no path traversal, no collisions, no
    silent corruption. Important for FileStore (which uses tenant in
    paths if we ever extend it that way) and for sanity in general."""

    @given(
        tenant_a=_tenant_ids,
        tenant_b=_tenant_ids,
        record_id=_record_ids,
    )
    @_settings
    def test_path_separators_dont_collide(self, make_store, tenant_a, tenant_b, record_id):
        """tenant '../etc/passwd' and tenant 'etc/passwd' must remain
        distinct buckets — even though one is the other prefixed."""
        store = make_store()
        assume(tenant_a != tenant_b)
        store.save("invoices", {"id": f"a_{record_id}", "tenant_id": tenant_a, "amount": 1})
        store.save("invoices", {"id": f"b_{record_id}", "tenant_id": tenant_b, "amount": 2})
        # Read each tenant's data — must NOT mingle.
        a_rows = store.query("invoices", tenant_id=tenant_a)
        b_rows = store.query("invoices", tenant_id=tenant_b)
        # Each row in A's results has tenant_a; same for B
        for r in a_rows:
            assert r["tenant_id"] == tenant_a
        for r in b_rows:
            assert r["tenant_id"] == tenant_b
        # The amounts are also segregated correctly
        a_amounts = sorted(r["amount"] for r in a_rows)
        b_amounts = sorted(r["amount"] for r in b_rows)
        assert 2 not in a_amounts, "tenant A leaked into B's data"
        assert 1 not in b_amounts, "tenant B leaked into A's data"

    def test_filestore_disallows_path_traversal_via_collection_name(self, tmp_path):
        """A collection name with '..' must NOT escape the root dir.
        FileStore-specific test — the file-on-disk layer has to be safe."""
        # Note: this checks `_path` doesn't write outside `root`.
        store = FileStore(tmp_path)
        # Try a clearly malicious collection name. Either it raises, or
        # any file it writes is sandboxed under tmp_path.
        try:
            store.save("../../../etc/sneaky", {"id": "x", "v": 1})
        except (OSError, ValueError):
            return  # acceptable: refused
        # If it didn't raise, no file should exist outside tmp_path
        outside = tmp_path.parent.parent.parent / "etc" / "sneaky.json"
        assert not outside.exists(), f"FileStore wrote OUTSIDE its root: {outside}"


# ── P4: update locality ────────────────────────────────────────────────
class TestUpdateLocality:
    @given(
        tenant_a=_tenant_ids,
        tenant_b=_tenant_ids,
        record_id=_record_ids,
        new_value=st.text(min_size=0, max_size=20),
    )
    @_settings
    def test_updating_one_doesnt_touch_other(
        self, make_store, tenant_a, tenant_b, record_id, new_value
    ):
        """If tenant A and B both have a record with the same id,
        updating A's record must not touch B's record."""
        store = make_store()
        assume(tenant_a != tenant_b)
        # Two records with same id (per ADR-0001 Store.save contract,
        # this overwrites — but they're stored under different
        # composite keys when callers responsibly include tenant_id in id).
        # The tenant_id field is the discriminator; records must include it.
        id_a = f"{tenant_a}::{record_id}"
        id_b = f"{tenant_b}::{record_id}"
        store.save("invoices", {"id": id_a, "tenant_id": tenant_a, "v": "before"})
        store.save("invoices", {"id": id_b, "tenant_id": tenant_b, "v": "before"})
        # Update only A's
        store.update("invoices", id_a, v=new_value)
        # B's unchanged
        b_row = store.get("invoices", id_b)
        assert b_row["v"] == "before", "updating tenant_a record corrupted tenant_b's record"
        # A's updated
        a_row = store.get("invoices", id_a)
        assert a_row["v"] == new_value


# ── P5: delete locality ────────────────────────────────────────────────
class TestDeleteLocality:
    @given(
        tenant_a=_tenant_ids,
        tenant_b=_tenant_ids,
        record_id=_record_ids,
    )
    @_settings
    def test_deleting_a_doesnt_delete_b(self, make_store, tenant_a, tenant_b, record_id):
        store = make_store()
        assume(tenant_a != tenant_b)
        id_a = f"{tenant_a}::{record_id}"
        id_b = f"{tenant_b}::{record_id}"
        store.save("invoices", {"id": id_a, "tenant_id": tenant_a})
        store.save("invoices", {"id": id_b, "tenant_id": tenant_b})
        store.delete("invoices", id_a)
        assert store.get("invoices", id_a) is None
        assert store.get("invoices", id_b) is not None, (
            "deleting tenant_a record cascaded to tenant_b"
        )


# ── Concrete real-world scenario: cache cross-tenant probe ─────────────
def test_cache_layer_tenant_isolation_concrete(make_store):
    """A concrete one-shot test that mirrors the acme cache layout.
    Property: writing to acme doesn't leak to acme-staging
    or acme-demo, even when business_dates overlap."""
    store = make_store()
    for tenant in ("acme", "acme-staging", "acme-demo"):
        for date in ("2026-04-29", "2026-04-30"):
            store.save(
                "pos_daily_cache",
                {
                    "id": f"{tenant}::{date}",
                    "tenant_id": tenant,
                    "business_date": date,
                    "data": {"revenue_cents": 100 if tenant == "acme" else 999},
                },
            )

    # Cross-tenant probe — must NEVER see other tenants' revenue
    for me in ("acme", "acme-staging", "acme-demo"):
        rows = store.query("pos_daily_cache", tenant_id=me)
        for r in rows:
            assert r["tenant_id"] == me
            # Revenue check pinned per tenant — no cross-pollination
            expected_rev = 100 if me == "acme" else 999
            assert r["data"]["revenue_cents"] == expected_rev
