"""FileStorage contract + LocalFileStorage tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from runspace.protocols.file_storage import FileMetadata, LocalFileStorage


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "files")


# ── round-trip ─────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_put_then_get_returns_same_bytes(self, storage):
        meta = storage.put("acme", "scan.pdf", b"hello world", content_type="application/pdf")
        assert storage.get("acme", meta.file_id) == b"hello world"

    def test_put_returns_metadata(self, storage):
        meta = storage.put("acme", "scan.pdf", b"abc", content_type="application/pdf")
        assert isinstance(meta, FileMetadata)
        assert meta.tenant_id == "acme"
        assert meta.original_name == "scan.pdf"
        assert meta.size_bytes == 3
        assert meta.content_type == "application/pdf"
        assert meta.sha256 == ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        assert meta.created_at  # ISO 8601, non-empty

    def test_metadata_returned_separately(self, storage):
        meta = storage.put("acme", "scan.pdf", b"abc")
        again = storage.metadata("acme", meta.file_id)
        assert again == meta


# ── tenant isolation ───────────────────────────────────────────────────


class TestTenantIsolation:
    def test_get_with_wrong_tenant_raises(self, storage):
        meta = storage.put("acme", "secret.pdf", b"private")
        with pytest.raises(FileNotFoundError):
            storage.get("other_tenant", meta.file_id)

    def test_metadata_with_wrong_tenant_raises(self, storage):
        meta = storage.put("acme", "secret.pdf", b"x")
        with pytest.raises(FileNotFoundError):
            storage.metadata("other_tenant", meta.file_id)

    def test_list_only_returns_own_tenant(self, storage):
        a = storage.put("tenant_a", "a.pdf", b"a")
        b = storage.put("tenant_b", "b.pdf", b"b")
        list_a = storage.list("tenant_a")
        list_b = storage.list("tenant_b")
        assert {m.file_id for m in list_a} == {a.file_id}
        assert {m.file_id for m in list_b} == {b.file_id}

    def test_path_traversal_in_tenant_id_rejected(self, storage):
        for bad in ("..", "../other", "/etc", "tenant/../other", ".hidden"):
            with pytest.raises(ValueError):
                storage.put(bad, "scan.pdf", b"x")

    def test_path_traversal_in_file_id_rejected_on_get(self, storage):
        with pytest.raises(ValueError):
            storage.get("acme", "../other_tenant/file")


# ── delete ─────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_returns_true_when_existed(self, storage):
        meta = storage.put("acme", "scan.pdf", b"x")
        assert storage.delete("acme", meta.file_id) is True
        with pytest.raises(FileNotFoundError):
            storage.get("acme", meta.file_id)

    def test_delete_idempotent_when_missing(self, storage):
        # File never existed → False, no exception
        assert storage.delete("acme", "abc123_nope.pdf") is False

    def test_delete_doesnt_cross_tenants(self, storage):
        a = storage.put("tenant_a", "a.pdf", b"a")
        # Try delete from wrong tenant — should be no-op for tenant_a
        storage.delete("tenant_b", a.file_id)
        # tenant_a's file still readable
        assert storage.get("tenant_a", a.file_id) == b"a"


# ── signed URL ─────────────────────────────────────────────────────────


class TestSignedURL:
    def test_signed_url_includes_tenant_and_file_id(self, storage):
        meta = storage.put("acme", "scan.pdf", b"x")
        url = storage.signed_url("acme", meta.file_id)
        assert "acme" in url
        assert meta.file_id in url

    def test_signed_url_validates_tenant(self, storage):
        with pytest.raises(ValueError):
            storage.signed_url("..", "anything")


# ── original-name handling ─────────────────────────────────────────────


class TestNameSanitization:
    def test_unsafe_chars_in_name_dont_break_storage(self, storage):
        # Original name preserved in metadata; file_id has sanitized form
        meta = storage.put(
            "acme",
            "CamScanner 25-04-2026 00.00.pdf",
            b"x",
        )
        assert meta.original_name == "CamScanner 25-04-2026 00.00.pdf"
        # Round-trip works despite spaces / dots
        assert storage.get("acme", meta.file_id) == b"x"

    def test_path_separator_in_name_stripped(self, storage):
        meta = storage.put("acme", "../etc/passwd", b"x")
        # Sanitized to a safe name, no escape attempted
        assert "/" not in meta.file_id
        assert ".." not in meta.file_id
