"""Widget-callback contract validator."""

from __future__ import annotations

import ast
import json
import logging
import re
from collections.abc import Iterable

log = logging.getLogger(__name__)

# Tool outputs are str(dict) of the Python tool return value, captured
# at runtimes/agentino.py via `tool_outputs.append(str(event.data))`.
# Reply fences are JSON literals the model emitted into its text.
_FENCE_RE = re.compile(r"```(?:datatable|chart)\s*\n(.*?)\n```", re.DOTALL)

# Fields that, when populated by a tool, the assistant MUST carry into
# its rendered fence. Add new contract fields here as the dispatcher
# contract grows (action buttons, form intents, etc.).
CALLBACK_FIELDS: tuple[str, ...] = (
    "row_links",
    "actions",
    "clickPromptKey",
    "point_links",
)


def _parse_loose(text: str) -> dict | None:
    """Tool outputs come in as `str(dict)` (Python repr). Try ast first
    for the repr case, fall back to JSON for tools that already
    pre-serialize. Return None on anything that isn't a dict."""
    text = text.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, SyntaxError):
        pass
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def _has_truthy(d: dict, field: str) -> bool:
    """`row_links: []` doesn't count — only a non-empty payload is a
    callback contract the assistant must honour."""
    return bool(d.get(field))


def _expected_fields(tool_outputs: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for out in tool_outputs:
        d = _parse_loose(out)
        if d is None:
            continue
        for f in CALLBACK_FIELDS:
            if _has_truthy(d, f):
                found.add(f)
    return found


def _present_fields(reply_text: str) -> set[str]:
    found: set[str] = set()
    for body in _FENCE_RE.findall(reply_text):
        try:
            obj = json.loads(body)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        for f in CALLBACK_FIELDS:
            if _has_truthy(obj, f):
                found.add(f)
    return found


def check_dropped(reply_text: str, tool_outputs: list[str]) -> list[str]:
    """Return the list of callback fields the tool emitted but the
    agent's fences omitted. Empty list = the contract held."""
    expected = _expected_fields(tool_outputs)
    if not expected:
        return []
    present = _present_fields(reply_text)
    return sorted(expected - present)


def warn_if_dropped(
    reply_text: str,
    tool_outputs: list[str] | None,
    *,
    agent_id: str = "?",
) -> list[str]:
    """Diagnostic-only check. Emits one warn log per dropped field set
    and returns the dropped list (callers can surface it in tests)."""
    if not tool_outputs:
        return []
    dropped = check_dropped(reply_text, tool_outputs)
    if dropped:
        log.warning(
            "[widget-contract] agent=%s dropped callback fields: %s — "
            "tool returned them but assistant fences omitted them. "
            "Strengthen SOUL.",
            agent_id,
            ", ".join(dropped),
        )
    return dropped
