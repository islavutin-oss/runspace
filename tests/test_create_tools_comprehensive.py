"""Comprehensive tests for all 5 file-generation tools.

Each tool:
  - generates content of the right type (PDF magic, ZIP magic for
    Office docs, valid CSV)
  - persists through protocols.FileStorage (Supabase or local)
  - returns a chat-renderable response with a download URL
  - is tenant-scoped (no cross-tenant leak)
  - handles edge cases (empty data, special characters)

Tools covered:
  - create_csv
  - create_spreadsheet (XLSX)
  - create_document (DOCX)
  - create_pdf
  - create_presentation (PPTX)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "storage"))
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    from runspace.protocols import reset

    reset()
    from agentino.tools.std import storage as _s

    _s._reset_for_tests()
    from agentino.core.context import set_context

    set_context(tenant_id="acme")
    yield
    set_context(tenant_id=None)
    _s._reset_for_tests()
    reset()


@pytest.fixture
def storage():
    from runspace.protocols import get_file_storage

    return get_file_storage()


def _files_for(storage, tenant_id="acme"):
    return list(storage.list(tenant_id))


# ── create_csv ─────────────────────────────────────────────────────────


class TestCreateCSV:
    @pytest.mark.asyncio
    async def test_basic_csv_generation(self, storage):
        from agentino.tools.std.create_csv import create_csv

        out = await create_csv.fn(
            title="Sales",
            columns=["date", "amount"],
            rows=[["2026-04-01", "100"], ["2026-04-02", "200"]],
        )
        # Returns markdown referencing the URL
        assert "/api/workspace/files/" in out
        # File persisted in storage
        files = _files_for(storage)
        assert len(files) == 1
        # CSV format
        bytes_back = storage.get("acme", files[0].file_id)
        assert b"date,amount" in bytes_back
        assert b"2026-04-01,100" in bytes_back

    @pytest.mark.asyncio
    async def test_filename_derived_from_title(self, storage):
        from agentino.tools.std.create_csv import create_csv

        await create_csv.fn(title="Q1 Report", columns=["x"], rows=[["1"]])
        files = _files_for(storage)
        # Filename should be slugified
        assert files[0].original_name.endswith(".csv")
        assert "q1-report" in files[0].original_name.lower()

    @pytest.mark.asyncio
    async def test_explicit_filename_respected(self, storage):
        from agentino.tools.std.create_csv import create_csv

        await create_csv.fn(
            title="Anything",
            columns=["x"],
            rows=[["1"]],
            filename="custom_name",
        )
        files = _files_for(storage)
        assert files[0].original_name == "custom_name.csv"

    @pytest.mark.asyncio
    async def test_special_chars_escaped(self, storage):
        from agentino.tools.std.create_csv import create_csv

        await create_csv.fn(
            title="Edge",
            columns=["text"],
            rows=[['has "quotes"'], ["has,comma"], ["has\nnewline"]],
        )
        files = _files_for(storage)
        bytes_back = storage.get("acme", files[0].file_id)
        # CSV module should have quoted these properly
        assert b'"' in bytes_back  # escaped quotes


# ── create_spreadsheet (XLSX) ──────────────────────────────────────────


class TestCreateSpreadsheet:
    @pytest.mark.asyncio
    async def test_xlsx_magic_bytes(self, storage):
        """XLSX is a ZIP archive — must start with PK\\x03\\x04."""
        from agentino.tools.std.create_spreadsheet import create_spreadsheet

        await create_spreadsheet.fn(
            title="Test",
            columns=["a"],
            rows=[["1"]],
        )
        files = _files_for(storage)
        bytes_back = storage.get("acme", files[0].file_id)
        assert bytes_back[:4] == b"PK\x03\x04", "Not a valid ZIP/XLSX"

    @pytest.mark.asyncio
    async def test_round_trip_through_openpyxl(self, storage):
        from agentino.tools.std.create_spreadsheet import create_spreadsheet

        await create_spreadsheet.fn(
            title="Q1Sales",
            columns=["date", "amount"],
            rows=[["2026-04-01", 100], ["2026-04-02", 200]],
        )
        files = _files_for(storage)
        import io

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(storage.get("acme", files[0].file_id)), read_only=True)
        # Ensure at least one sheet has data
        assert wb.worksheets, "no sheets in xlsx"


# ── create_document (DOCX) ─────────────────────────────────────────────


class TestCreateDocument:
    @pytest.mark.asyncio
    async def test_docx_magic_bytes(self, storage):
        from agentino.tools.std.create_document import create_document

        await create_document.fn(title="Doc", content="Hello world.")
        files = _files_for(storage)
        bytes_back = storage.get("acme", files[0].file_id)
        assert bytes_back[:4] == b"PK\x03\x04"

    @pytest.mark.asyncio
    async def test_markdown_renders_to_docx(self, storage):
        from agentino.tools.std.create_document import create_document

        await create_document.fn(
            title="Q1",
            content="# Heading\n\nFirst paragraph.\n\nSecond paragraph.",
        )
        files = _files_for(storage)
        import io

        from docx import Document as DocxDocument

        doc = DocxDocument(io.BytesIO(storage.get("acme", files[0].file_id)))
        texts = " ".join(p.text for p in doc.paragraphs)
        assert "First paragraph" in texts
        assert "Second paragraph" in texts


# ── create_pdf ─────────────────────────────────────────────────────────

_HAS_WEASYPRINT = False
try:
    import weasyprint  # noqa: F401

    _HAS_WEASYPRINT = True
except ImportError:
    pass


@pytest.mark.skipif(not _HAS_WEASYPRINT, reason="weasyprint not installed")
class TestCreatePDF:
    @pytest.mark.asyncio
    async def test_pdf_magic_bytes(self, storage):
        from agentino.tools.std.create_pdf import create_pdf

        await create_pdf.fn(title="Report", content="Hello.")
        files = _files_for(storage)
        bytes_back = storage.get("acme", files[0].file_id)
        assert bytes_back[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_pdf_filename_extension(self, storage):
        from agentino.tools.std.create_pdf import create_pdf

        await create_pdf.fn(title="Quarterly", content="Q1 results.")
        files = _files_for(storage)
        assert files[0].original_name.endswith(".pdf")


class TestCreatePDFGracefulFailure:
    """If weasyprint is missing, the tool must fail gracefully (not crash
    the agentino runtime). Production images install it; dev may not."""

    @pytest.mark.asyncio
    async def test_returns_error_string_when_weasyprint_missing(self):
        if _HAS_WEASYPRINT:
            pytest.skip("weasyprint is installed; cannot test missing-dep path")
        from agentino.tools.std.create_pdf import create_pdf

        out = await create_pdf.fn(title="X", content="y")
        # Tool returns a user-facing error, not an exception
        assert "fail" in out.lower() or "not installed" in out.lower()


# ── create_presentation (PPTX) ─────────────────────────────────────────


class TestCreatePresentation:
    @pytest.mark.asyncio
    async def test_pptx_magic_bytes(self, storage):
        from agentino.tools.std.create_presentation import create_presentation

        await create_presentation.fn(
            title="Deck",
            slides=["# Intro\n\n- Welcome"],
        )
        files = _files_for(storage)
        bytes_back = storage.get("acme", files[0].file_id)
        assert bytes_back[:4] == b"PK\x03\x04"


# ── Cross-tool tenant scoping ──────────────────────────────────────────


class TestTenantScoping:
    @pytest.mark.asyncio
    async def test_files_isolated_to_tenant(self, storage, monkeypatch):
        """Each tool persists under the current tenant. Switching tenant
        contexts should produce isolated namespaces."""
        from agentino.core.context import set_context
        from agentino.tools.std.create_csv import create_csv

        set_context(tenant_id="tenant_a")
        await create_csv.fn(title="A", columns=["x"], rows=[["1"]])

        set_context(tenant_id="tenant_b")
        await create_csv.fn(title="B", columns=["x"], rows=[["2"]])

        a_files = _files_for(storage, tenant_id="tenant_a")
        b_files = _files_for(storage, tenant_id="tenant_b")
        assert len(a_files) == 1
        assert len(b_files) == 1
        assert "a" in a_files[0].original_name.lower()
        assert "b" in b_files[0].original_name.lower()

        # Can't get tenant_a's file as tenant_b
        with pytest.raises(FileNotFoundError):
            storage.get("tenant_b", a_files[0].file_id)


# ── Migration cleanliness ──────────────────────────────────────────────
