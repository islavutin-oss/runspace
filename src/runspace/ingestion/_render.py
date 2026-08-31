"""Per-channel reply rendering."""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

# Tunables — tighter for groups (more eyes, less attention per message)
# than for DMs. The "top-N rows" cap exists because Telegram messages
# top out at 4096 chars and a 50-row datatable from Otto eats the
# whole budget.
_DM_ROWS_CAP = 10
_GROUP_ROWS_CAP = 5

# Match a fenced block opening with ```datatable or ```chart on its own
# line, then everything up to the next closing ``` on its own line.
# Non-greedy body so adjacent fences don't get merged into one match.
_FENCE_RE = re.compile(
    r"```(datatable|chart)\s*\n(.*?)\n```",
    re.DOTALL,
)


def transform_for_telegram(
    text: str,
    *,
    chat_type: str = "private",
    workspace_url: str | None = None,
) -> str:
    """Strip mcp-ui fences and replace with Telegram-friendly text.

    Args:
        text: agent reply, possibly containing ```datatable```/```chart```
            blocks interleaved with prose.
        chat_type: Telegram `chat.type` — "private" gets a wider table
            (DM, single reader, can scroll); anything else (groups,
            supergroups, channels, unknown) gets the narrower cap. The
            unknown case errs on the side of less text — better to
            truncate more than to risk hitting Telegram's 4096-char
            limit and have the whole reply rejected.
        workspace_url: optional absolute URL (e.g. https://acme.example)
            to prepend to relative `row_links` so a Telegram user can
            tap through to the rich web view. If None, no deep-links
            are emitted.

    Returns:
        A string safe to send via Telegram's sendMessage. Free of
        ```datatable``` / ```chart``` fences. Surrounding prose, plain
        markdown, and other code fences pass through unchanged.

    Malformed JSON inside a fence is logged and replaced with a short
    placeholder — never raises, since the caller is the bot loop and
    crashing means the user sees nothing at all.
    """
    if not text:
        return text

    rows_cap = _DM_ROWS_CAP if chat_type == "private" else _GROUP_ROWS_CAP

    def _replace(match: re.Match[str]) -> str:
        kind, body = match.group(1), match.group(2)
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError) as e:
            log.warning("[telegram-render] %s fence: bad JSON (%s); dropping", kind, e)
            return f"_({kind} unavailable)_"
        try:
            if kind == "datatable":
                return _render_datatable(parsed, rows_cap=rows_cap, workspace_url=workspace_url)
            return _render_chart(parsed, workspace_url=workspace_url)
        except Exception as e:  # noqa: BLE001 — never crash the bot loop
            log.warning("[telegram-render] %s fence: render failed (%s); dropping", kind, e)
            return f"_({kind} unavailable)_"

    return _FENCE_RE.sub(_replace, text)


# ── datatable ─────────────────────────────────────────────────────────


def _render_datatable(parsed: dict, *, rows_cap: int, workspace_url: str | None) -> str:
    """Render a parsed datatable envelope to a Telegram markdown block.

    Format:
        **<title>**            ← if title set
        | col | col |
        | --- | --- |
        | a   | b   |
        ...
        _+N more rows_         ← if truncated
        [Open in workspace](https://…/<row_links[0]>)  ← if base URL + first link

    Long cells are truncated at 32 chars to avoid runaway widths. Pipes
    inside cells are escaped so they don't break the table.
    """
    title = parsed.get("title")
    columns = parsed.get("columns") or []
    rows = parsed.get("rows") or []
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ValueError("columns/rows must be lists")

    rendered = rows[:rows_cap]
    truncated = max(len(rows) - rows_cap, 0)

    lines: list[str] = []
    if title:
        lines.append(f"**{_escape_md(str(title))}**")

    if columns:
        header = "| " + " | ".join(_cell(c) for c in columns) + " |"
        sep = "| " + " | ".join("---" for _ in columns) + " |"
        lines.append(header)
        lines.append(sep)
    for row in rendered:
        if not isinstance(row, list):
            continue
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")

    if truncated:
        lines.append(f"_+{truncated} more row{'s' if truncated != 1 else ''}_")

    deep_link = _datatable_deep_link(parsed, workspace_url)
    if deep_link:
        lines.append(deep_link)

    return "\n".join(lines)


def _datatable_deep_link(parsed: dict, workspace_url: str | None) -> str | None:
    """Find the first usable row_link and turn it into an absolute URL.

    Returns None when no base URL is configured or no row exposes a
    link — Telegram will just show the table without a tap-through.
    """
    if not workspace_url:
        return None
    links = parsed.get("row_links") or []
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, str) and link.startswith("/"):
            return f"[Open in workspace]({workspace_url.rstrip('/')}{link})"
    return None


# ── chart ─────────────────────────────────────────────────────────────


def _render_chart(parsed: dict, *, workspace_url: str | None) -> str:
    """One-line summary of a chart envelope.

    Both shapes are accepted:
        {data: [{x, y, ...}], yKey, ...}
        {labels: [...], datasets: [{label, data: []}]}
    """
    title = parsed.get("title") or "Chart"
    series = _chart_series(parsed)
    head = f"📈 **{_escape_md(str(title))}**"
    if not series:
        return head
    first, last = series[0], series[-1]
    summary = f"{head}: {_fmt(first)} → {_fmt(last)}"
    if len(series) > 2:
        summary += f"  _({len(series)} points)_"
    # No deep-link for charts in this iteration — chart points carry
    # `clickPromptKey` for in-chat drill-through on the web, which has
    # no Telegram analogue without inline keyboards (deferred to a
    # later ADR).
    return summary


def _chart_series(parsed: dict) -> list[tuple[str, float | None]]:
    """Extract a flat (label, value) list from the various chart shapes."""
    data = parsed.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        x_key = parsed.get("xKey") or _guess_label_key(data[0])
        y_key = parsed.get("yKey") or _guess_value_key(data[0], x_key)
        out: list[tuple[str, float | None]] = []
        for d in data:
            label = str(d.get(x_key, ""))
            v = d.get(y_key)
            try:
                out.append((label, float(v) if v is not None else None))
            except (TypeError, ValueError):
                out.append((label, None))
        return out

    labels = parsed.get("labels")
    datasets = parsed.get("datasets") or parsed.get("series")
    if isinstance(labels, list) and isinstance(datasets, list) and datasets:
        first_ds = datasets[0]
        if isinstance(first_ds, dict):
            ds_data = first_ds.get("data") or []
            return [
                (str(label), _to_float(ds_data[i] if i < len(ds_data) else None))
                for i, label in enumerate(labels)
            ]
    return []


def _guess_label_key(row: dict) -> str:
    for candidate in ("date", "label", "x", "name", "category"):
        if candidate in row:
            return candidate
    return next(iter(row.keys()), "x")


def _guess_value_key(row: dict, exclude: str) -> str:
    for k, v in row.items():
        if k == exclude:
            continue
        if isinstance(v, (int, float)):
            return k
    for k in row.keys():
        if k != exclude:
            return k
    return "y"


def _to_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── small helpers ─────────────────────────────────────────────────────


_CELL_LIMIT = 32


def _cell(v) -> str:
    """Markdown-table cell: pipes escaped, newlines folded, length capped."""
    s = str(v if v is not None else "")
    s = s.replace("\n", " ").replace("|", "\\|")
    if len(s) > _CELL_LIMIT:
        s = s[: _CELL_LIMIT - 1] + "…"
    return s


def _escape_md(s: str) -> str:
    return s.replace("*", "\\*").replace("_", "\\_")


def _fmt(point: tuple[str, float | None]) -> str:
    label, value = point
    if value is None:
        return label
    if value == int(value):
        return f"{label} ({int(value)})"
    return f"{label} ({value:.1f})"
