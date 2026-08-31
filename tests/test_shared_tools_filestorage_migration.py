"""End-to-end tests for the FileStorage migration of agent_tools.

Covers list_files + read_file. Each tool now reads through
protocols.FileStorage instead of `/tmp/workspace-files/` directly.

These tests guarantee:
  - Tenant scoping (tenant A's file invisible to tenant B)
  - Resolution by file_id AND by original_name
  - Format-specific readers still work after the path swap
  - No legacy /tmp/workspace-files/ leak
  - Empty workspace handled gracefully
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
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    from runspace.protocols import reset

    reset()
    from agentino.core.context import set_context

    set_context(tenant_id="acme")
    yield
    set_context(tenant_id=None)
    reset()


# ── list_files ─────────────────────────────────────────────────────────


class TestListFiles:
    @pytest.mark.asyncio
    async def test_empty_workspace(self):
        from agentino.tools.std.list_files import list_files

        assert "No files" in await list_files.fn()

    @pytest.mark.asyncio
    async def test_lists_files_for_current_tenant(self):
        from agentino.tools.std.list_files import list_files

        from runspace.protocols import get_file_storage

        fs = get_file_storage()
        fs.put("acme", "report.pdf", b"x" * 100, content_type="application/pdf")
        fs.put("acme", "data.csv", b"y" * 50, content_type="text/csv")
        out = await list_files.fn()
        assert "report.pdf" in out
        assert "data.csv" in out
        assert "Workspace files (2)" in out

    @pytest.mark.asyncio
    async def test_does_not_list_other_tenants_files(self):
        from agentino.tools.std.list_files import list_files

        from runspace.protocols import get_file_storage

        fs = get_file_storage()
        fs.put("other_tenant", "secret.pdf", b"private")
        # We're in acme context (set in fixture)
        out = await list_files.fn()
        assert "secret.pdf" not in out
        assert "No files" in out  # acme has nothing

    @pytest.mark.asyncio
    async def test_file_size_formatting(self):
        from agentino.tools.std.list_files import list_files

        from runspace.protocols import get_file_storage

        fs = get_file_storage()
        fs.put("acme", "tiny.txt", b"x" * 100)
        fs.put("acme", "medium.txt", b"x" * 5000)
        fs.put("acme", "big.txt", b"x" * (2 * 1024 * 1024))
        out = await list_files.fn()
        assert "100 B" in out
        assert "4.9 KB" in out
        assert "2.0 MB" in out


# ── read_file ──────────────────────────────────────────────────────────


class TestReadFile:
    @pytest.mark.asyncio
    async def test_returns_not_found_when_missing(self):
        from agentino.tools.std.read_file import read_file

        out = await read_file.fn("nonexistent.csv")
        assert "not found" in out.lower()

    @pytest.mark.asyncio
    async def test_reads_csv_round_trip(self):
        from agentino.tools.std.read_file import read_file

        from runspace.protocols import get_file_storage

        fs = get_file_storage()
        csv_bytes = b"name,age\nAlice,30\nBob,25\n"
        fs.put("acme", "people.csv", csv_bytes, content_type="text/csv")
        out = await read_file.fn("people.csv")
        assert "Alice" in out
        assert "Bob" in out
        assert "datatable" in out

    @pytest.mark.asyncio
    async def test_reads_text_file(self):
        from agentino.tools.std.read_file import read_file

        from runspace.protocols import get_file_storage

        fs = get_file_storage()
        fs.put("acme", "note.txt", b"hello world", content_type="text/plain")
        out = await read_file.fn("note.txt")
        assert "hello world" in out

    @pytest.mark.asyncio
    async def test_resolves_by_file_id(self):
        """LLM may pass either filename or file_id — both must resolve."""
        from agentino.tools.std.read_file import read_file

        from runspace.protocols import get_file_storage

        fs = get_file_storage()
        meta = fs.put("acme", "data.json", b'{"k": 1}', content_type="application/json")
        out = await read_file.fn(meta.file_id)
        assert "k" in out

    @pytest.mark.asyncio
    async def test_does_not_leak_other_tenants_files(self):
        """Critical: tenant A's file is invisible to tenant B."""
        from agentino.tools.std.read_file import read_file

        from runspace.protocols import get_file_storage

        fs = get_file_storage()
        fs.put("other_tenant", "secret.txt", b"private")
        # Currently in acme (fixture)
        out = await read_file.fn("secret.txt")
        assert "not found" in out.lower()

    @pytest.mark.asyncio
    async def test_unsupported_format_message(self):
        from agentino.tools.std.read_file import read_file

        from runspace.protocols import get_file_storage

        fs = get_file_storage()
        fs.put("acme", "binary.exe", b"\x00\x01\x02", content_type="application/octet-stream")
        out = await read_file.fn("binary.exe")
        assert "Unsupported" in out or "not supported" in out.lower()


# ── Migration regression: no legacy paths ──────────────────────────────


class TestMigrationCleanliness:
    def test_storage_does_not_reference_legacy_path(self):
        import agentino.tools.std.storage as m

        src = Path(m.__file__).read_text()
        assert "/tmp/workspace-files" not in src
