"""Auto-render Markdown docs for every agent's tool surface in a tenant.

Walks `apps[]` in workspace.yml, introspects each agentino agent's tools
via `agents_inspect.inspect_app`, and writes one Markdown file per agent
to an output directory. Useful for:

  - Public-facing docs of "what each agent can do" (drop into a docs site)
  - PR review of changes that affect agent tool surfaces (commit the rendered
    docs alongside the .py changes — the diff makes intent visible)
  - Operator hand-off (no Python required to read what's exposed)

Usage:
  python3 runspace/scripts/render_tool_docs.py tenants/acme/workspace.yml \\
      --out-dir docs/agents/acme/
  python3 runspace/scripts/render_tool_docs.py <ws.yml> --stdout  # one doc to stdout

Output: `<out-dir>/<agent_id>.md` per agent, plus an index `README.md`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure runspace + the inspector module are importable
RUNSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(RUNSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNSPACE_ROOT))
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from agents_inspect import inspect_workspace  # noqa: E402


def render_agent_md(catalog: dict[str, Any], app: dict[str, Any]) -> str:
    """Render one agent's tool surface as Markdown."""
    lines: list[str] = []
    title = app["name"] if app.get("name") and app["name"] != app["id"] else app["id"]
    lines.append(f"# {title}")
    lines.append("")
    if app.get("role"):
        lines.append(f"_{app['role']}_")
        lines.append("")

    lines.append("## Identity")
    lines.append("")
    lines.append(f"- **Tenant**: {catalog['tenant']} (`{catalog['tenant_id']}`)")
    lines.append(f"- **App ID**: `{app['id']}`")
    lines.append(f"- **Runtime**: `{app['type']}`")
    lines.append(f"- **Model**: `{app['model']}`")
    if app.get("soul"):
        lines.append(f"- **SOUL**: `{app['soul']}`")
    lines.append("")

    if app.get("type") != "agentino":
        lines.append("## Tools")
        lines.append("")
        lines.append(
            f"_(`{app['type']}` runtime — tools come from the runtime's "
            f"built-in surface, not from a `tools/` directory.)_"
        )
        lines.append("")
        return "\n".join(lines)

    tools = app.get("tools") or []
    lines.append(f"## Tools ({len(tools)})")
    lines.append("")
    if app.get("tools_error"):
        lines.append(f"> **Inspection error**: `{app['tools_error']}`")
        lines.append("")
        return "\n".join(lines)
    if not tools:
        lines.append("_No `@tool`-decorated functions found in the agent's `tools_dir`._")
        lines.append("")
        return "\n".join(lines)

    if app.get("tools_dir"):
        lines.append(f"_Source: `{app['tools_dir']}`_")
        lines.append("")

    for tool in tools:
        sig = ", ".join(
            f"{p['name']}: {p['type']}" + ("" if p["required"] else " = ?") for p in tool["params"]
        )
        lines.append(f"### `{tool['name']}({sig})`")
        lines.append("")
        if tool.get("description"):
            lines.append(tool["description"].strip())
            lines.append("")
        if tool["params"]:
            lines.append("| Param | Type | Required | Description |")
            lines.append("|---|---|---|---|")
            for p in tool["params"]:
                desc = (p.get("description") or "").replace("\n", " ").replace("|", "\\|")
                lines.append(
                    f"| `{p['name']}` | `{p['type']}` | "
                    f"{'yes' if p['required'] else 'no'} | {desc} |"
                )
            lines.append("")

    return "\n".join(lines)


def render_index_md(catalog: dict[str, Any]) -> str:
    """Render an index page linking to each agent doc."""
    lines: list[str] = []
    lines.append(f"# {catalog['tenant']}")
    lines.append("")
    lines.append(
        f"Tenant tool catalog — auto-generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')}."
    )
    lines.append("")
    lines.append(f"- **Tenant ID**: `{catalog['tenant_id']}`")
    lines.append(f"- **Apps**: {len(catalog['apps'])}")
    lines.append(f"- **Providers**: {', '.join(catalog['providers']) or '—'}")
    lines.append("")
    lines.append("## Agents")
    lines.append("")
    lines.append("| Agent | Role | Runtime | Tools |")
    lines.append("|---|---|---|---|")
    for app in catalog["apps"]:
        title = app["name"] if app.get("name") and app["name"] != app["id"] else app["id"]
        link = f"[{title}](./{app['id']}.md)"
        n_tools = len(app.get("tools") or [])
        tools_cell = f"{n_tools}" if app["type"] == "agentino" else f"_{app['type']}_"
        lines.append(f"| {link} | {app.get('role', '')} | `{app['type']}` | {tools_cell} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("workspace_yml", help="Path to a tenant's workspace.yml")
    p.add_argument("--out-dir", "-o", help="Output directory for per-agent .md files")
    p.add_argument("--agent", "-a", help="Comma-separated subset of agent ids to render")
    p.add_argument(
        "--stdout", action="store_true", help="Print rendered docs to stdout (concatenated)"
    )
    args = p.parse_args()

    workspace_yml = Path(args.workspace_yml).resolve()
    if not workspace_yml.exists():
        print(f"Error: workspace.yml not found: {workspace_yml}", file=sys.stderr)
        sys.exit(2)

    if not args.stdout and not args.out_dir:
        print("Error: pass --out-dir <path> or --stdout", file=sys.stderr)
        sys.exit(2)

    agent_filter = args.agent.split(",") if args.agent else None
    catalog = inspect_workspace(workspace_yml, agent_filter)

    if args.stdout:
        for app in catalog["apps"]:
            print(render_agent_md(catalog, app))
            print("\n---\n")
        return

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for app in catalog["apps"]:
        target = out_dir / f"{app['id']}.md"
        target.write_text(render_agent_md(catalog, app))
        written.append(target.name)
    (out_dir / "README.md").write_text(render_index_md(catalog))
    written.append("README.md")
    print(f"Wrote {len(written)} files to {out_dir}/")
    for n in written:
        print(f"  • {n}")


if __name__ == "__main__":
    main()
