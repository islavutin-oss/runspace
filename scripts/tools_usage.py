"""Query the tool-usage JSONL log.

Usage:
  python3 runspace/scripts/tools_usage.py                       # last 7 days, all
  python3 runspace/scripts/tools_usage.py --since 2026-05-01    # from a date
  python3 runspace/scripts/tools_usage.py --tenant acme     # one tenant
  python3 runspace/scripts/tools_usage.py --tenant acme --by tool
  python3 runspace/scripts/tools_usage.py --json                # raw rows out

Reads `~/.runspace/tool_usage.jsonl` (or RUNSPACE_TOOL_USAGE_PATH env var).

Recording is opt-in: set `registry.record_tool_usage = True` in your
gateway / AppRegistry construction. Off by default to keep tests
side-effect-free.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure runspace is importable
RUNSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(RUNSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNSPACE_ROOT))

from runspace.workspace.backend.tools_usage import (  # noqa: E402
    DEFAULT_USAGE_PATH,
    aggregate_by,
    query,
)

DIM = "\033[2m"
G = "\033[32m"
B = "\033[34m"
RESET = "\033[0m"


def _resolve_since(arg: str | None) -> str | None:
    """Accept ISO date, ISO timestamp, or '<N>d'/'<N>h' relative."""
    if not arg:
        return None
    if arg.endswith("d") and arg[:-1].isdigit():
        delta = timedelta(days=int(arg[:-1]))
        return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    if arg.endswith("h") and arg[:-1].isdigit():
        delta = timedelta(hours=int(arg[:-1]))
        return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    if "T" in arg:
        return arg if arg.endswith("Z") else arg + "Z"
    # Bare YYYY-MM-DD → start of that day UTC
    return f"{arg}T00:00:00Z"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Query tool-usage telemetry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--since", help="ISO date / timestamp / '7d' / '24h' (default: 7d)", default="7d"
    )
    p.add_argument("--until", help="ISO date / timestamp")
    p.add_argument("--tenant", help="Filter to one tenant")
    p.add_argument("--agent", help="Filter to one agent")
    p.add_argument("--tool", help="Filter to one tool name")
    p.add_argument(
        "--by",
        choices=["tool", "agent", "tenant", "agent_tool", "tenant_agent"],
        help="Aggregate counts by this dimension",
    )
    p.add_argument("--path", help=f"Override JSONL path (default: {DEFAULT_USAGE_PATH})")
    p.add_argument("--json", action="store_true", help="Emit raw matched rows as JSON")
    args = p.parse_args()

    path = Path(args.path) if args.path else DEFAULT_USAGE_PATH
    if not path.exists():
        print(f"{DIM}No telemetry log at {path}.{RESET}")
        print(f"{DIM}Telemetry is opt-in — set `registry.record_tool_usage = True`{RESET}")
        sys.exit(0)

    rows = query(
        since=_resolve_since(args.since),
        until=_resolve_since(args.until),
        tenant=args.tenant,
        agent=args.agent,
        tool=args.tool,
        path=path,
    )

    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return

    print(f"{B}Tool usage{RESET} {DIM}from {path}{RESET}")
    filters = []
    if args.since:
        filters.append(f"since={args.since}")
    if args.tenant:
        filters.append(f"tenant={args.tenant}")
    if args.agent:
        filters.append(f"agent={args.agent}")
    if args.tool:
        filters.append(f"tool={args.tool}")
    if filters:
        print(f"{DIM}filters:{RESET} {'  '.join(filters)}")
    print(f"{DIM}rows:{RESET} {len(rows)}\n")

    if not rows:
        return

    if args.by:
        keys = {
            "tool": ("tool",),
            "agent": ("agent",),
            "tenant": ("tenant",),
            "agent_tool": ("agent", "tool"),
            "tenant_agent": ("tenant", "agent"),
        }[args.by]
        agg = aggregate_by(rows, *keys)
        agg_sorted = sorted(agg.items(), key=lambda kv: -kv[1])
        header = " / ".join(keys).upper()
        print(f"  {header:<40}  COUNT")
        print(f"  {'-' * 40}  -----")
        for k, count in agg_sorted:
            label = " / ".join(str(v or "—") for v in k)
            print(f"  {label:<40}  {count:>5}")
        return

    # Default: per-row chronological dump
    print(f"  {'TS':<22} {'TENANT':<12} {'AGENT':<12} {'TOOL':<22} {'MS':>6}")
    print(f"  {'-' * 22} {'-' * 12} {'-' * 12} {'-' * 22} {'-' * 6}")
    for row in rows[-50:]:  # cap to last 50 for terminal sanity
        ts = row.get("ts", "")[:19]
        tenant = (row.get("tenant") or "—")[:12]
        agent = (row.get("agent") or "—")[:12]
        tool = (row.get("tool") or "—")[:22]
        ms = row.get("turn_elapsed_ms", "")
        print(f"  {ts:<22} {tenant:<12} {agent:<12} {tool:<22} {ms:>6}")
    if len(rows) > 50:
        print(f"  {DIM}… {len(rows) - 50} more (use --json for full dump){RESET}")


if __name__ == "__main__":
    main()
