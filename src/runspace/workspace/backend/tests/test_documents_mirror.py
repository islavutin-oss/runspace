"""Regression: chat /upload must mirror to the platform's `documents` table so chat-uploaded files appear on the Documents page."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# Imported for its side effect: monkeypatch resolves the dotted target by
# attribute walk, and a submodule only becomes an attribute of its parent
# once something has imported it.
import runspace.helpers.documents.store  # noqa: F401
from runspace.workspace.backend.gateway import WorkspaceGateway


@pytest.fixture
def gw():
    g = WorkspaceGateway(name="Test", tenant_id="acme")
    return g


def test_mirror_inserts_correct_envelope(gw, monkeypatch):
    """The row written to documents must carry tenant_id, the chat
    bucket marker, the file_id as storage_path, and source='chat'
    so the Documents UI can distinguish chat-bridged files.

    The mirror goes through `tools.documents.store.
    get_document_store(...).insert(row)` rather than direct supabase —
    we patch that store factory instead.
    """
    captured: dict = {}

    fake_store = MagicMock()
    fake_store.insert.side_effect = lambda row: captured.update(row=row) or row
    monkeypatch.setattr(
        "runspace.helpers.documents.store.get_document_store",
        lambda tenant_id: fake_store,
    )
    # Env vars are still required by the early-return guard at the
    # top of _mirror_to_documents_table, even though the actual write
    # no longer goes through supabase directly.
    monkeypatch.setenv("SUPABASE_URL", "http://fake")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")

    gw._mirror_to_documents_table(
        tenant_id="acme",
        file_id="abc12345_Acme_Supplies_Ltd.pdf",
        original_name="Acme_Supplies_Ltd.pdf",
        size_bytes=567259,
        content_type="application/pdf",
    )

    row = captured["row"]
    assert row["tenant_id"] == "acme"
    assert row["bucket"] == "chat"
    assert row["storage_path"] == "abc12345_Acme_Supplies_Ltd.pdf"
    assert row["filename"] == "Acme_Supplies_Ltd.pdf"
    assert row["source"] == "chat"
    assert row["mime"] == "application/pdf"
    assert row["size_bytes"] == 567259


def test_mirror_skips_when_supabase_unconfigured(gw, monkeypatch):
    """No SUPABASE_URL/KEY → silently skip. Local dev / sandbox
    environments must not have the upload route blow up because
    they don't talk to Supabase."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    # Should not raise
    gw._mirror_to_documents_table(
        tenant_id="acme",
        file_id="x_y.pdf",
        original_name="y.pdf",
        size_bytes=1,
        content_type="application/pdf",
    )


def test_mirror_swallows_insert_errors(gw, monkeypatch, caplog):
    """If the insert raises (RLS, network, missing column), we log
    and continue. Aborting the upload because the mirror failed
    would be worse than silently dropping the documents row — the
    file still reached FileStorage."""
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.side_effect = RuntimeError("RLS denied")
    monkeypatch.setenv("SUPABASE_URL", "http://fake")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")
    monkeypatch.setattr("supabase.create_client", lambda url, key: mock_sb)

    # Must NOT raise
    gw._mirror_to_documents_table(
        tenant_id="acme",
        file_id="x_y.pdf",
        original_name="y.pdf",
        size_bytes=1,
        content_type="application/pdf",
    )
