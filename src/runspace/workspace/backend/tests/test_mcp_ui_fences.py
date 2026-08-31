"""Every fence the frontend renders must survive the round trip."""

from __future__ import annotations

import pytest

from runspace.workspace.backend._mcp_ui import (
    begin_turn,
    register_block,
    restore_mcp_ui_blocks,
)

CANONICAL = {
    "chart": '```chart\n{"type": "bar", "data": [1, 2, 3]}\n```',
    "datatable": '```datatable\n{"columns": ["a"], "rows": [[1]]}\n```',
    "kpi": '```kpi\n{"label": "Revenue", "value": "42"}\n```',
    "insight": '```insight\n{"headline": "Five branches drive 12%"}\n```',
}


@pytest.fixture(autouse=True)
def _fresh_turn():
    begin_turn()
    yield
    begin_turn()


@pytest.mark.parametrize("fence", sorted(CANONICAL))
def test_a_mangled_fence_is_replaced_in_place(fence):
    """Splicing must happen where the model put the fence.

    Asserting only that the canonical block appears somewhere is not enough:
    an unrecognised fence type still gets its block appended at the end, so
    a substring check passes while the mangled copy is left on the page and
    the widget renders an error above the real one.
    """
    placeholder = register_block(CANONICAL[fence])
    assert placeholder.startswith(f"```{fence}")
    mangled = f"Here you go:\n\n```{fence}\nnot valid at all\n```\n\nTail."
    out = restore_mcp_ui_blocks(mangled)
    assert CANONICAL[fence] in out
    assert "not valid at all" not in out, "the mangled fence survived"
    assert out.index(CANONICAL[fence]) < out.index("Tail."), "block was appended, not spliced"


@pytest.mark.parametrize("fence", sorted(CANONICAL))
def test_a_dropped_fence_is_appended(fence):
    register_block(CANONICAL[fence])
    assert CANONICAL[fence] in restore_mcp_ui_blocks("I did the analysis.")


def test_a_fence_with_no_registered_block_is_left_alone():
    """A model writing its own mermaid diagram, or an insight nothing
    registered, must reach the browser untouched."""
    register_block(CANONICAL["chart"])
    text = "```mermaid\ngraph TD; A-->B;\n```\n\n```chart\nmangled\n```"
    out = restore_mcp_ui_blocks(text)
    assert "```mermaid\ngraph TD; A-->B;\n```" in out
    assert CANONICAL["chart"] in out


def test_ordering_is_preserved_within_a_type():
    first = '```chart\n{"id": 1}\n```'
    second = '```chart\n{"id": 2}\n```'
    register_block(first)
    register_block(second)
    out = restore_mcp_ui_blocks("```chart\nx\n```\nand\n```chart\ny\n```")
    assert out.index(first) < out.index(second)
