"""Regression: chat context must expose the file's REAL original_name."""

from __future__ import annotations

import pytest

from runspace.workspace.backend.attachments import _resolve_files


@pytest.fixture
def storage_local(tmp_path, monkeypatch):
    """Wire the protocol layer to LocalFileStorage rooted in tmp_path."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "storage"))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    from runspace.protocols import get_file_storage, reset

    reset()
    return get_file_storage()


def test_chat_context_uses_real_original_name_with_spaces(storage_local):
    """The exact 2026-05-02 case: upload with spaces, derived chat
    context must mention the spaces, NOT the underscored file_id."""
    meta = storage_local.put(
        "acme",
        "CamScanner 25-04-2026 00.00.pdf",
        b"%PDF-1.4 fake",
        content_type="application/pdf",
    )
    # Sanity: file_id was sanitized but original_name was preserved
    assert " " not in meta.file_id
    assert meta.original_name == "CamScanner 25-04-2026 00.00.pdf"

    context, _audio_b64, _audio_mime = _resolve_files(
        file_ids=[meta.file_id],
        attachments=[],
        tenant_id="acme",
    )
    # The LLM-facing context must contain the SPACED name so the
    # downstream tool's exact-match lookup against meta.original_name
    # succeeds.
    assert "CamScanner 25-04-2026 00.00.pdf" in context
    assert "CamScanner_25-04-2026_00.00.pdf" not in context


def test_chat_context_uses_original_name_for_typical_filenames(storage_local):
    """Plain filenames (no spaces) still flow through unchanged."""
    meta = storage_local.put(
        "acme",
        "scan.pdf",
        b"%PDF-1.4 fake",
        content_type="application/pdf",
    )
    context, _, _ = _resolve_files([meta.file_id], [], tenant_id="acme")
    assert "scan.pdf" in context


# NOTE: the cross-repo round-trip test (chat context → acme's
# process_invoice._resolve_to_temp_path) was moved to
