"""Widget-callback contract validator tests."""

from __future__ import annotations

import logging

from runspace.workspace.backend.widget_validator import (
    CALLBACK_FIELDS,
    check_dropped,
    warn_if_dropped,
)

# ─── happy paths ────────────────────────────────────────────────────


def test_no_tool_outputs_returns_clean() -> None:
    assert check_dropped("hello", []) == []


def test_tool_without_callback_fields_clean() -> None:
    """Tools that don't emit callback fields shouldn't trigger
    anything — most read-only tools fall here."""
    out = "{'count': 5, 'items': [1, 2, 3]}"
    assert check_dropped("here are 5", [out]) == []


def test_row_links_present_in_fence_clean() -> None:
    """Tool returned row_links and assistant carried them through.
    This is the contract holding."""
    tool_out = "{'invoices': [{'id': 'i01'}], 'row_links': ['/a/i01']}"
    reply = (
        "5 invoices:\n\n"
        "```datatable\n"
        '{"columns": ["ID"], "rows": [["i01"]], "row_links": ["/a/i01"]}\n'
        "```\n"
    )
    assert check_dropped(reply, [tool_out]) == []


# ─── failure modes — the whole point of this module ────────────────


def test_row_links_dropped_is_flagged() -> None:
    """Nova 2026-05-08 case: tool emitted row_links, assistant
    rendered a datatable WITHOUT them. Must be detected."""
    tool_out = "{'row_links': ['/a/i01', '/a/i02']}"
    reply = '```datatable\n{"columns": ["ID"], "rows": [["i01"], ["i02"]]}\n```\n'
    assert check_dropped(reply, [tool_out]) == ["row_links"]


def test_dropped_field_logs_warning(caplog) -> None:
    """The hook in gateway.py relies on the warn-level log to surface
    in container logs. Lock that contract."""
    tool_out = "{'row_links': ['/a/i01']}"
    reply = '```datatable\n{"columns": [], "rows": []}\n```'
    with caplog.at_level(logging.WARNING, logger="runspace.workspace.backend.widget_validator"):
        dropped = warn_if_dropped(reply, [tool_out], agent_id="nova")
    assert dropped == ["row_links"]
    assert any(
        "widget-contract" in r.message and "nova" in r.message and "row_links" in r.message
        for r in caplog.records
    )


def test_multiple_dropped_fields_all_flagged() -> None:
    tool_out = "{'row_links': ['/a/1'], 'actions': [['ok']], 'point_links': ['x']}"
    reply = '```datatable\n{"columns": ["ID"], "rows": [["1"]]}\n```\n'
    assert check_dropped(reply, [tool_out]) == sorted(["row_links", "actions", "point_links"])


# ─── edge cases ────────────────────────────────────────────────────


def test_empty_callback_list_doesnt_trigger() -> None:
    """A tool that returns `row_links: []` (empty list) hasn't asked
    the agent to do anything — don't flag."""
    tool_out = "{'row_links': []}"
    reply = '```datatable\n{"columns": [], "rows": []}\n```'
    assert check_dropped(reply, [tool_out]) == []


def test_unparseable_tool_output_skipped() -> None:
    """If a tool returns a non-dict (string, error blob), the
    validator should silently skip — not crash."""
    assert check_dropped("hello", ["just a string", "error: timeout"]) == []


def test_chart_fence_also_inspected() -> None:
    """Callback fields can appear in chart fences too (clickPromptKey,
    point_links). Must inspect both fence kinds."""
    tool_out = "{'clickPromptKey': 'prompt'}"
    # Reply has a chart fence WITHOUT the key.
    reply = '```chart\n{"type": "bar", "data": [{"x": 1, "y": 2}], "xKey": "x", "yKey": "y"}\n```\n'
    assert check_dropped(reply, [tool_out]) == ["clickPromptKey"]


def test_warn_if_dropped_returns_empty_when_no_outputs() -> None:
    """Cheap path: no tool outputs at all (text-only agent reply).
    Don't even parse fences."""
    assert warn_if_dropped("hello", None) == []
    assert warn_if_dropped("hello", []) == []


def test_callback_fields_constant_is_canonical() -> None:
    """Lock the public list — adding a new field is intentional and
    must come with a test that exercises it."""
    assert CALLBACK_FIELDS == (
        "row_links",
        "actions",
        "clickPromptKey",
        "point_links",
    )
