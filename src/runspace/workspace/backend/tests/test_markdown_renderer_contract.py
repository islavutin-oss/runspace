"""Contract tests for the shared MarkdownContent.tsx renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

# `tests/` is at workspace/backend/tests, so parents[3] reaches the
# runspace repo root regardless of where the tests are invoked from.
# <repo>/workspace/frontend, outside the Python package — see the note in
# test_frontend_layering.py. parents[3] resolved inside src/runspace, so
# this contract skipped instead of running.
SHARED_ROOT = Path(__file__).resolve().parents[5]
MARKDOWN_TSX = (
    SHARED_ROOT / "workspace" / "frontend" / "shared" / "components" / "MarkdownContent.tsx"
)


@pytest.fixture(scope="module")
def markdown_source() -> str:
    if not MARKDOWN_TSX.is_file():
        pytest.skip(f"MarkdownContent.tsx not found at {MARKDOWN_TSX}")
    return MARKDOWN_TSX.read_text(encoding="utf-8")


def test_remark_gfm_is_imported_and_used(markdown_source):
    """Without `remark-gfm`, GitHub-flavored markdown tables (the `|---|`
    syntax) render as raw text. Every analytics/finance message uses them."""
    assert "remark-gfm" in markdown_source, "must import remark-gfm"
    assert "remarkGfm" in markdown_source, "must pass remarkGfm to ReactMarkdown plugins"


def test_special_language_blocks_are_dispatched(markdown_source):
    """`chart` / `datatable` / `mermaid` code blocks aren't plain markdown —
    they render as interactive widgets via dedicated components. Losing
    these handlers means the user sees JSON/code blob instead of a chart
    or table. Lock down the language names that MarkdownContent.tsx
    explicitly recognizes."""
    for lang in ("language-chart", "language-datatable", "language-mermaid"):
        assert lang in markdown_source, (
            f"MarkdownContent.tsx must handle '{lang}' — without it the "
            f"corresponding code blocks render as raw text"
        )
