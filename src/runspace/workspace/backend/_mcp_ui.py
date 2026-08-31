"""mcp-ui block integrity."""

from __future__ import annotations

import re
import threading

# Every fence the frontend renders as a component. A type with no canonical
# block registered this turn is left exactly as the model wrote it, so adding
# a type here is safe even when no tool emits it.
_FENCE_RE = re.compile(r"```(chart|datatable|kpi|insight|form|file)\n(.*?)\n```", re.S)

# Per-turn ordered list of canonical blocks. A plain module global (not a
# contextvar) because agentino runs sync @tool functions in a thread pool
_lock = threading.Lock()
_turn_blocks: list[str] = []


def begin_turn() -> None:
    with _lock:
        _turn_blocks.clear()


def register_block(block: str) -> str:
    """Record a full ```...``` block for this turn; return a placeholder
    fence for the model to position. Positional splice lines up by type."""
    with _lock:
        _turn_blocks.append(block)
        idx = len(_turn_blocks) - 1
    m = re.match(r"```(\w+)", block.strip())
    fence_type = m.group(1) if m else "chart"
    return f'```{fence_type}\n{{"$mcpui": {idx}}}\n```'


def _take_turn() -> list[str]:
    with _lock:
        lst = list(_turn_blocks)
        _turn_blocks.clear()
    return lst


def restore_mcp_ui_blocks(text: str, tool_outputs: list[str] | None = None) -> str:
    """Splice the turn's canonical blocks over the model's fences, by type
    and emission order. Appends any blocks the model omitted entirely."""
    blocks = _take_turn()
    # Fallback for runtimes that didn't call begin_turn: recover full blocks
    # from tool_outputs (works only when they weren't truncated).
    if not blocks and tool_outputs:
        for out in tool_outputs:
            for m in _FENCE_RE.finditer(out or ""):
                blocks.append(m.group(0))
    if not blocks or not text:
        return text

    # Group canonical blocks by fence type, preserving order.
    by_type: dict[str, list[str]] = {}
    for b in blocks:
        m = re.match(r"```(\w+)", b.strip())
        by_type.setdefault(m.group(1) if m else "chart", []).append(b)

    counters = {k: 0 for k in by_type}

    def _repl(m: re.Match) -> str:
        t = m.group(1)
        lst = by_type.get(t)
        if lst and counters[t] < len(lst):
            block = lst[counters[t]]
            counters[t] += 1
            return block
        return m.group(0)

    new_text = _FENCE_RE.sub(_repl, text)

    appended: list[str] = []
    for t, lst in by_type.items():
        while counters[t] < len(lst):
            appended.append(lst[counters[t]])
            counters[t] += 1
    if appended:
        new_text = new_text.rstrip() + "\n\n" + "\n\n".join(appended)
    return new_text
