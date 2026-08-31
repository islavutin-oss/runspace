"""Diff tool surfaces between two runspace tenant catalogs.

Surfaces what changed in an agent's exposed tools between two snapshots —
useful as a deploy-time artifact ("Ada's surface gained `mark_invoice_void`,
removed `set_invoice_status`") so unintended changes don't sneak through.

Two modes:

  Snapshot:
    runspace/scripts/tools_diff.py snapshot tenants/acme/workspace.yml \\
        --out snapshots/acme.json

  Diff:
    runspace/scripts/tools_diff.py diff snapshots/acme.json \\
        tenants/acme/workspace.yml

Recommended CI flow:
  1. Commit a baseline `snapshots/<tenant>.json` per tenant alongside
     workspace.yml.
  2. On every deploy, run `tools_diff.py diff <baseline> <ws.yml>`.
  3. Exit code 0 = no surface change. Exit code 1 = changes detected;
     CI prints a structured diff and either blocks the deploy or surfaces
     it as an artifact for human review.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Reuse the inspector — single source of truth
RUNSPACE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = Path(__file__).resolve().parent
for p in (RUNSPACE_ROOT, SCRIPTS_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agents_inspect import inspect_workspace  # noqa: E402

G = "\033[32m"
R = "\033[31m"
Y = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def _index_apps(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {app["id"]: app for app in catalog.get("apps", [])}


def _index_tools(app: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in app.get("tools") or []}


def _params_signature(tool: dict[str, Any]) -> str:
    """A single string capturing param names + types + required-ness.

    Used as a coarse signature: signature changes when names, types, or
    required-ness changes — not when descriptions are reworded.
    """
    parts = []
    for p in tool.get("params") or []:
        marker = "" if p.get("required") else "?"
        parts.append(f"{p['name']}:{p.get('type', 'any')}{marker}")
    return "(" + ", ".join(parts) + ")"


def diff_catalogs(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return a structured diff between two catalogs.

    Shape:
        {
          "tenant_changed": bool,
          "apps_added":   [{"id", "name", "n_tools"}],
          "apps_removed": [{"id", "name"}],
          "apps_changed": [{
            "id", "name",
            "tools_added":   [{"name", "signature"}],
            "tools_removed": [{"name"}],
            "tools_changed": [{
              "name", "before_signature", "after_signature"
            }],
          }],
          "summary": {  # bare counts, useful in CI logs
            "added_apps": int, "removed_apps": int, "changed_apps": int,
            "added_tools": int, "removed_tools": int, "changed_tools": int,
          },
        }
    """
    a_idx = _index_apps(before)
    b_idx = _index_apps(after)

    a_ids = set(a_idx.keys())
    b_ids = set(b_idx.keys())

    added_apps = [
        {"id": i, "name": b_idx[i].get("name", i), "n_tools": len(b_idx[i].get("tools") or [])}
        for i in sorted(b_ids - a_ids)
    ]
    removed_apps = [{"id": i, "name": a_idx[i].get("name", i)} for i in sorted(a_ids - b_ids)]

    apps_changed: list[dict[str, Any]] = []
    for app_id in sorted(a_ids & b_ids):
        a_tools = _index_tools(a_idx[app_id])
        b_tools = _index_tools(b_idx[app_id])

        added = [
            {"name": n, "signature": _params_signature(b_tools[n])}
            for n in sorted(set(b_tools) - set(a_tools))
        ]
        removed = [{"name": n} for n in sorted(set(a_tools) - set(b_tools))]
        changed = []
        for n in sorted(set(a_tools) & set(b_tools)):
            sig_a = _params_signature(a_tools[n])
            sig_b = _params_signature(b_tools[n])
            if sig_a != sig_b:
                changed.append(
                    {
                        "name": n,
                        "before_signature": sig_a,
                        "after_signature": sig_b,
                    }
                )
        if added or removed or changed:
            apps_changed.append(
                {
                    "id": app_id,
                    "name": b_idx[app_id].get("name", app_id),
                    "tools_added": added,
                    "tools_removed": removed,
                    "tools_changed": changed,
                }
            )

    summary = {
        "added_apps": len(added_apps),
        "removed_apps": len(removed_apps),
        "changed_apps": len(apps_changed),
        "added_tools": sum(len(c["tools_added"]) for c in apps_changed),
        "removed_tools": sum(len(c["tools_removed"]) for c in apps_changed),
        "changed_tools": sum(len(c["tools_changed"]) for c in apps_changed),
    }

    return {
        "tenant_changed": before.get("tenant_id") != after.get("tenant_id"),
        "apps_added": added_apps,
        "apps_removed": removed_apps,
        "apps_changed": apps_changed,
        "summary": summary,
    }


def render_diff(diff: dict[str, Any]) -> str:
    """Pretty-print a diff for terminal viewing."""
    lines: list[str] = []
    s = diff["summary"]
    if all(v == 0 for v in s.values()):
        lines.append(f"{G}No tool-surface changes.{RESET}")
        return "\n".join(lines)

    if diff["tenant_changed"]:
        lines.append(
            f"{Y}⚠ tenant_id differs between snapshots — diff still computed by app id.{RESET}"
        )
        lines.append("")

    for app in diff["apps_added"]:
        lines.append(f"{G}+ app{RESET}    {app['id']} ({app['name']})  [{app['n_tools']} tools]")
    for app in diff["apps_removed"]:
        lines.append(f"{R}- app{RESET}    {app['id']} ({app['name']})")

    for app in diff["apps_changed"]:
        if not (app["tools_added"] or app["tools_removed"] or app["tools_changed"]):
            continue
        lines.append("")
        lines.append(f"{DIM}~{RESET} app    {app['id']} ({app['name']})")
        for t in app["tools_added"]:
            lines.append(f"  {G}+ {t['name']}{t['signature']}{RESET}")
        for t in app["tools_removed"]:
            lines.append(f"  {R}- {t['name']}{RESET}")
        for t in app["tools_changed"]:
            lines.append(f"  {Y}~ {t['name']}{RESET}")
            lines.append(f"      before: {t['before_signature']}")
            lines.append(f"      after:  {t['after_signature']}")

    lines.append("")
    lines.append(
        f"{DIM}summary:{RESET} "
        f"+{s['added_apps']} apps, -{s['removed_apps']} apps, "
        f"~{s['changed_apps']} apps  |  "
        f"+{s['added_tools']} tools, -{s['removed_tools']} tools, "
        f"~{s['changed_tools']} tools"
    )
    return "\n".join(lines)


def cmd_snapshot(args: argparse.Namespace) -> int:
    workspace_yml = Path(args.workspace_yml).resolve()
    if not workspace_yml.exists():
        print(f"Error: {workspace_yml} not found", file=sys.stderr)
        return 2
    catalog = inspect_workspace(workspace_yml)
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(catalog, indent=2, default=str))
    print(f"Wrote snapshot: {out_path}")
    print(f"  tenant: {catalog['tenant']} ({catalog['tenant_id']})")
    print(f"  apps:   {len(catalog['apps'])}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    before_path = Path(args.before).resolve()
    if not before_path.exists():
        print(f"Error: baseline snapshot not found: {before_path}", file=sys.stderr)
        return 2
    workspace_yml = Path(args.after).resolve()
    if not workspace_yml.exists():
        print(f"Error: workspace.yml not found: {workspace_yml}", file=sys.stderr)
        return 2

    before = json.loads(before_path.read_text())
    after = inspect_workspace(workspace_yml)
    diff = diff_catalogs(before, after)

    if args.json:
        print(json.dumps(diff, indent=2, default=str))
    else:
        print(render_diff(diff))

    s = diff["summary"]
    has_changes = any(v > 0 for v in s.values())
    if has_changes and args.fail_on_change:
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description="Snapshot and diff agent tool surfaces.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Write a baseline snapshot to JSON")
    snap.add_argument("workspace_yml", help="Path to a tenant's workspace.yml")
    snap.add_argument("--out", required=True, help="Path to write the snapshot JSON")
    snap.set_defaults(_handler=cmd_snapshot)

    df = sub.add_parser("diff", help="Diff a baseline snapshot against the current workspace.yml")
    df.add_argument("before", help="Baseline snapshot JSON (from `snapshot`)")
    df.add_argument("after", help="Current workspace.yml to compare against")
    df.add_argument("--json", action="store_true", help="Emit raw diff JSON")
    df.add_argument(
        "--fail-on-change", action="store_true", help="Exit 1 when any change is detected (CI gate)"
    )
    df.set_defaults(_handler=cmd_diff)

    args = p.parse_args()
    sys.exit(args._handler(args))


if __name__ == "__main__":
    main()
