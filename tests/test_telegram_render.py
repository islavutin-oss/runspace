"""Unit tests for Telegram per-channel adapter (`_render.py`).

These tests pin the contract:
- ```datatable``` / ```chart``` fences are stripped from Telegram-bound text
- A degraded markdown form takes their place
- Surrounding prose passes through verbatim
- Malformed input never crashes the bot loop
"""

from __future__ import annotations

import json

import pytest

from runspace.ingestion._render import transform_for_telegram

# ── happy-path: datatable ─────────────────────────────────────────────


def _wrap_datatable(payload: dict) -> str:
    return "```datatable\n" + json.dumps(payload) + "\n```"


def test_datatable_replaced_with_markdown_table():
    payload = {
        "title": "Low stock",
        "columns": ["Item", "Stock", "Reorder"],
        "rows": [
            ["Tomato", "2 kg", "10"],
            ["Olive oil", "1 L", "5"],
        ],
    }
    out = transform_for_telegram(_wrap_datatable(payload), chat_type="private")
    assert "```datatable" not in out
    assert "**Low stock**" in out
    assert "| Item | Stock | Reorder |" in out
    assert "| Tomato | 2 kg | 10 |" in out
    assert "| Olive oil | 1 L | 5 |" in out


def test_datatable_truncates_to_dm_cap():
    payload = {
        "columns": ["n"],
        "rows": [[str(i)] for i in range(15)],  # cap is 10 for DM
    }
    out = transform_for_telegram(_wrap_datatable(payload), chat_type="private")
    assert "| 0 |" in out
    assert "| 9 |" in out
    assert "| 10 |" not in out
    assert "+5 more rows" in out


def test_datatable_truncates_tighter_in_groups():
    payload = {
        "columns": ["n"],
        "rows": [[str(i)] for i in range(15)],  # cap is 5 for groups
    }
    out = transform_for_telegram(_wrap_datatable(payload), chat_type="supergroup")
    assert "| 4 |" in out
    assert "| 5 |" not in out
    assert "+10 more rows" in out


def test_datatable_deep_link_when_workspace_url_set():
    payload = {
        "columns": ["Item"],
        "rows": [["Tomato"]],
        "row_links": ["/workspace/inventory?focus=tomato"],
    }
    out = transform_for_telegram(
        _wrap_datatable(payload),
        chat_type="private",
        workspace_url="https://acme.example",
    )
    assert "[Open in workspace](https://acme.example/workspace/inventory?focus=tomato)" in out


def test_datatable_no_deep_link_without_workspace_url():
    payload = {
        "columns": ["Item"],
        "rows": [["Tomato"]],
        "row_links": ["/workspace/inventory?focus=tomato"],
    }
    out = transform_for_telegram(_wrap_datatable(payload), chat_type="private")
    assert "Open in workspace" not in out


def test_datatable_deep_link_skipped_for_non_relative_links():
    """row_links that aren't path-relative (e.g. external URLs, blanks)
    don't get treated as in-workspace deep-links."""
    payload = {
        "columns": ["Item"],
        "rows": [["Tomato"], ["Olive"]],
        "row_links": ["https://example.com/x", ""],
    }
    out = transform_for_telegram(
        _wrap_datatable(payload),
        chat_type="private",
        workspace_url="https://acme.example",
    )
    assert "Open in workspace" not in out


def test_datatable_long_cells_get_truncated():
    payload = {
        "columns": ["x"],
        "rows": [["a" * 80]],
    }
    out = transform_for_telegram(_wrap_datatable(payload), chat_type="private")
    # cell capped at 32 chars including ellipsis
    assert "a" * 80 not in out
    assert "…" in out


def test_datatable_cell_pipes_escaped():
    payload = {"columns": ["x"], "rows": [["a|b"]]}
    out = transform_for_telegram(_wrap_datatable(payload), chat_type="private")
    assert "a\\|b" in out


# ── happy-path: chart ─────────────────────────────────────────────────


def _wrap_chart(payload: dict) -> str:
    return "```chart\n" + json.dumps(payload) + "\n```"


def test_chart_replaced_with_summary_data_form():
    payload = {
        "title": "Daily covers",
        "data": [
            {"date": "2026-05-01", "covers": 40},
            {"date": "2026-05-02", "covers": 65},
            {"date": "2026-05-03", "covers": 50},
        ],
    }
    out = transform_for_telegram(_wrap_chart(payload), chat_type="private")
    assert "```chart" not in out
    assert "📈" in out
    assert "Daily covers" in out
    assert "2026-05-01" in out
    assert "2026-05-03" in out
    assert "(3 points)" in out


def test_chart_replaced_with_summary_labels_datasets_form():
    payload = {
        "title": "Revenue",
        "labels": ["Mon", "Tue", "Wed"],
        "datasets": [{"label": "EUR", "data": [100, 220, 180]}],
    }
    out = transform_for_telegram(_wrap_chart(payload), chat_type="private")
    assert "📈" in out
    assert "Mon (100)" in out
    assert "Wed (180)" in out


def test_chart_with_empty_data_is_just_a_title():
    payload = {"title": "Nothing yet", "data": []}
    out = transform_for_telegram(_wrap_chart(payload), chat_type="private")
    assert "📈" in out
    assert "Nothing yet" in out


# ── prose interleaving ────────────────────────────────────────────────


def test_prose_around_fences_preserved():
    text = (
        "Here are today's alerts:\n\n"
        + _wrap_datatable({"columns": ["x"], "rows": [["y"]]})
        + "\n\nLet me know if you'd like to restock."
    )
    out = transform_for_telegram(text, chat_type="private")
    assert "Here are today's alerts:" in out
    assert "Let me know if you'd like to restock." in out
    assert "```datatable" not in out
    assert "| x |" in out
    assert "| y |" in out


def test_no_fences_pass_through_verbatim():
    text = "Just a plain reply, no widgets here.\n\n- bullet\n- list\n"
    assert transform_for_telegram(text, chat_type="private") == text


def test_other_code_fences_left_alone():
    text = "Run:\n```bash\nls -la\n```\nDone."
    out = transform_for_telegram(text, chat_type="private")
    assert "```bash" in out
    assert "ls -la" in out


def test_multiple_fences_all_replaced():
    text = (
        _wrap_datatable({"columns": ["a"], "rows": [["1"]]})
        + "\n\nAnd a chart:\n\n"
        + _wrap_chart({"title": "T", "data": [{"x": "Mon", "y": 1}]})
    )
    out = transform_for_telegram(text, chat_type="private")
    assert "```datatable" not in out
    assert "```chart" not in out
    assert "| a |" in out
    assert "📈" in out


# ── failure modes — must never raise ──────────────────────────────────


def test_malformed_json_drops_fence_with_placeholder():
    text = "Header\n\n```datatable\n{not valid json\n```\n\nFooter"
    out = transform_for_telegram(text, chat_type="private")
    assert "Header" in out
    assert "Footer" in out
    assert "```datatable" not in out
    assert "(datatable unavailable)" in out


def test_malformed_chart_drops_fence_with_placeholder():
    text = "```chart\n{still bad\n```"
    out = transform_for_telegram(text, chat_type="private")
    assert "```chart" not in out
    assert "(chart unavailable)" in out


def test_unexpected_shape_does_not_crash():
    # rows is not a list — would raise inside _render_datatable
    text = "```datatable\n" + json.dumps({"columns": "x", "rows": "y"}) + "\n```"
    out = transform_for_telegram(text, chat_type="private")
    assert "(datatable unavailable)" in out


def test_empty_string_passes_through():
    assert transform_for_telegram("", chat_type="private") == ""


def test_unknown_chat_type_uses_narrow_cap():
    """Unknown chat_type falls through to the narrower cap. Better to
    over-truncate than to risk hitting Telegram's 4096-char ceiling."""
    payload = {"columns": ["n"], "rows": [[str(i)] for i in range(15)]}
    out = transform_for_telegram(_wrap_datatable(payload), chat_type="weirdtype")
    assert "| 4 |" in out
    assert "| 5 |" not in out
    assert "+10 more rows" in out


# ── regression guard: agent reply with realistic shape ────────────────


def test_realistic_ada_reply_strips_widgets():
    """Sanity check on a plausible Ada reply: prose intro + datatable."""
    reply = (
        "Here's what's due today:\n\n"
        "```datatable\n"
        + json.dumps(
            {
                "title": "Due today",
                "columns": ["Supplier", "Amount", "IBAN"],
                "rows": [
                    ["Acme_Supplies_Ltd", "€103.94", "CY07009005..."],
                    ["GVOL Corporation", "€2,400.00", "CY01234..."],
                ],
                "row_links": [
                    "/workspace/accounting?invoice=abc",
                    "/workspace/accounting?invoice=def",
                ],
            }
        )
        + "\n```\n\nWant me to mark any as paid?"
    )
    out = transform_for_telegram(
        reply,
        chat_type="private",
        workspace_url="https://acme.example",
    )
    assert "Here's what's due today:" in out
    assert "Want me to mark any as paid?" in out
    assert "**Due today**" in out
    assert "Acme_Supplies_Ltd" in out
    assert "[Open in workspace](https://acme.example/workspace/accounting?invoice=abc)" in out
    assert "```datatable" not in out
    assert "row_links" not in out  # raw JSON didn't leak


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
