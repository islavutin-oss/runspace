"""Hypothesis property tests for FileStorage."""

from __future__ import annotations

import string
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from runspace.protocols.file_storage import LocalFileStorage

# Tenant ids: non-empty, no path separators, no leading dot
safe_tenant = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=32,
).filter(lambda s: s and not s.startswith(".") and s not in ("..",))


# Original names: anything printable; sanitization is the SUT
arbitrary_name = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),  # exclude surrogates
    min_size=1,
    max_size=200,
)


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "files")


class TestFuzzTenantSafety:
    @given(tenant=safe_tenant, name=arbitrary_name, content=st.binary(min_size=1, max_size=4096))
    @settings(
        max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_round_trip_for_any_safe_tenant(self, tmp_path, tenant, name, content):
        """For any safe tenant_id and any printable name, put/get works."""
        import secrets

        storage = LocalFileStorage(tmp_path / f"r_{secrets.token_hex(6)}")
        meta = storage.put(tenant, name, content)
        assert storage.get(tenant, meta.file_id) == content

    @given(
        bad=st.sampled_from(
            [
                "",
                "..",
                ".",
                "/etc",
                "/",
                "../other",
                "tenant/../escape",
                ".hidden",
                "/absolute",
                "back\\slash",
                "with\x00null",
            ]
        )
    )
    @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_unsafe_tenant_id_always_raises(self, storage, bad):
        """No matter what unsafe value an attacker passes, ValueError."""
        with pytest.raises(ValueError):
            storage.put(bad, "scan.pdf", b"x")


class TestFuzzNameSanitization:
    @given(name=arbitrary_name)
    @settings(
        max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_name_does_not_escape_root(self, storage, name):
        """No matter what crazy filename the user uploads, the resulting
        file_id is safe — no path separators, no parent-directory refs."""
        meta = storage.put("acme", name, b"x")
        assert "/" not in meta.file_id
        assert "\\" not in meta.file_id
        assert ".." not in meta.file_id
        # File actually exists where the storage says
        assert storage.get("acme", meta.file_id) == b"x"


class TestFuzzCrossTenant:
    @given(t_a=safe_tenant, t_b=safe_tenant, content_a=st.binary(min_size=1, max_size=128))
    @settings(
        max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_cross_tenant_read_never_succeeds(self, tmp_path, t_a, t_b, content_a):
        """For any two safe tenant ids (even if one is a prefix of the
        other), tenant_b cannot read tenant_a's files via tenant_a's
        file_id."""
        if t_a == t_b:
            return  # nothing to assert when the same tenant
        import secrets

        storage = LocalFileStorage(tmp_path / f"x_{secrets.token_hex(6)}")
        meta = storage.put(t_a, "secret.pdf", content_a)
        with pytest.raises(FileNotFoundError):
            storage.get(t_b, meta.file_id)


class TestFuzzListing:
    @given(
        tenant=safe_tenant,
        files=st.lists(
            st.tuples(arbitrary_name, st.binary(min_size=1, max_size=64)), min_size=0, max_size=8
        ),
    )
    @settings(
        max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_list_returns_only_writes(self, tmp_path, tenant, files):
        """list() reflects exactly what was put, no more, no less."""
        import secrets

        storage = LocalFileStorage(tmp_path / f"l_{secrets.token_hex(6)}")
        ids = []
        for name, content in files:
            meta = storage.put(tenant, name, content)
            ids.append(meta.file_id)
        listed = storage.list(tenant)
        assert {m.file_id for m in listed} == set(ids)


class TestFuzzDelete:
    @given(name=arbitrary_name, content=st.binary(min_size=1, max_size=64))
    @settings(
        max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_delete_then_get_raises(self, storage, name, content):
        meta = storage.put("acme", name, content)
        assert storage.delete("acme", meta.file_id) is True
        with pytest.raises(FileNotFoundError):
            storage.get("acme", meta.file_id)
        # Idempotent
        assert storage.delete("acme", meta.file_id) is False
