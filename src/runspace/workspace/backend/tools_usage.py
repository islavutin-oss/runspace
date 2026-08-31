"""Tool-usage telemetry — append tool-call events to a JSONL log."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_USAGE_PATH = Path(
    os.environ.get("RUNSPACE_TOOL_USAGE_PATH", str(Path.home() / ".runspace" / "tool_usage.jsonl"))
)

# File writes are not async-safe across threads; serialise.
_WRITE_LOCK = threading.Lock()


def _now_iso() -> str:
    # Z-suffix UTC, second precision — readable + fits a single line cleanly
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_tool_calls(
    *,
    tenant: str | None,
    agent: str,
    session_key: str | None,
    tools: Iterable[str],
    turn_elapsed_ms: int | None = None,
    path: Path | None = None,
) -> int:
    """Append one JSONL line per tool call. Returns the number of lines written.

    Failures are logged at WARNING and swallowed — tool-usage telemetry MUST
    NOT break the agent loop. Calling this with `tools=[]` is a no-op
    (returns 0).
    """
    target = Path(path) if path else DEFAULT_USAGE_PATH
    rows = list(tools)
    if not rows:
        return 0
    ts = _now_iso()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for tool_name in rows:
            entry = {
                "ts": ts,
                "tenant": tenant,
                "agent": agent,
                "tool": str(tool_name),
                "session_key": session_key,
            }
            if turn_elapsed_ms is not None:
                entry["turn_elapsed_ms"] = int(turn_elapsed_ms)
            lines.append(json.dumps(entry, default=str))
        with _WRITE_LOCK:
            with target.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        return len(lines)
    except OSError as e:
        log.warning("[tool_usage] failed to append to %s: %s", target, e)
        return 0


def query(
    *,
    since: str | None = None,
    until: str | None = None,
    tenant: str | None = None,
    agent: str | None = None,
    tool: str | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read tool_usage.jsonl, filter, return rows. Tolerates malformed lines.

    Args:
        since/until: ISO-8601 timestamp strings (Z-suffixed, second precision).
            Compared lexicographically — works for the format we write.
        tenant/agent/tool: exact-match filters.
    """
    target = Path(path) if path else DEFAULT_USAGE_PATH
    if not target.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since and row.get("ts", "") < since:
                    continue
                if until and row.get("ts", "") > until:
                    continue
                if tenant and row.get("tenant") != tenant:
                    continue
                if agent and row.get("agent") != agent:
                    continue
                if tool and row.get("tool") != tool:
                    continue
                out.append(row)
    except OSError as e:
        log.warning("[tool_usage] failed to read %s: %s", target, e)
    return out


def aggregate_by(rows: list[dict[str, Any]], *keys: str) -> dict[tuple, int]:
    """Group rows by the named keys, count. Useful for the CLI summary view."""
    counts: dict[tuple, int] = {}
    for row in rows:
        k = tuple(row.get(key, "") for key in keys)
        counts[k] = counts.get(k, 0) + 1
    return counts
