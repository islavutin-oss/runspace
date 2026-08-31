"""Inspect the tool surface of every agent in a runspace tenant's workspace.yml.

For each app with `tools_dir` (or `tools:` in workspace.yml), introspects
every `@tool`-decorated function via agentino's existing
`discover_tools_from_dir()` and prints name + signature + docstring.

Usage:
  python3 runspace/scripts/agents_inspect.py tenants/acme/workspace.yml
  python3 runspace/scripts/agents_inspect.py <ws.yml> --agent accountant
  python3 runspace/scripts/agents_inspect.py <ws.yml> --json   # machine-readable

Closes the "what tools does Ada have?" discoverability gap — answer used to
require grepping `tenants/acme/agents/accountant/tools/*.py`; now it's
one command.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# Ensure runspace + agentino are importable
RUNSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(RUNSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNSPACE_ROOT))

G = "\033[32m"
B = "\033[34m"
DIM = "\033[2m"
RESET = "\033[0m"


def _resolve_tools_dir(workspace_yml: Path, app_cfg: dict[str, Any]) -> Path | None:
    """Resolve workspace.yml's `tools:` (relative to workspace.yml's parent)."""
    tools_rel = app_cfg.get("tools")
    if not tools_rel:
        return None
    return (workspace_yml.parent / tools_rel).resolve()


def _introspect_tool(tool: Any) -> dict[str, Any]:
    """Extract a small summary dict from an agentino Tool object.

    Defensive against schema variation across agentino versions.
    """
    name = getattr(tool, "name", "") or ""
    description = getattr(tool, "description", "") or ""
    schema = getattr(tool, "schema", None) or {}
    # OpenAI-style schema: {"function": {"parameters": {"properties": {...}}}}
    params: list[dict[str, Any]] = []
    if isinstance(schema, dict):
        fn = schema.get("function") or {}
        props = (fn.get("parameters") or {}).get("properties") or {}
        required = set((fn.get("parameters") or {}).get("required") or [])
        for pname, pspec in props.items():
            params.append(
                {
                    "name": pname,
                    "type": pspec.get("type", "any"),
                    "required": pname in required,
                    "description": pspec.get("description", ""),
                }
            )
    return {"name": name, "description": description, "params": params}


def inspect_app(workspace_yml: Path, app_id: str, app_cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a per-app catalog: {id, name, type, model, tools: [...]}."""
    summary = {
        "id": app_id,
        "name": app_cfg.get("name", app_id),
        "role": app_cfg.get("role", ""),
        "type": app_cfg.get("type", "agentino"),
        "model": app_cfg.get("model", "(workspace default)"),
        "soul": app_cfg.get("soul", ""),
        "tools": [],
    }
    if app_cfg.get("type", "agentino") != "agentino":
        return summary  # CLI-harness runtimes don't carry typed tools

    tools_dir = _resolve_tools_dir(workspace_yml, app_cfg)
    if not tools_dir or not tools_dir.exists():
        return summary

    try:
        from agentino.config.tools_yaml import discover_tools_from_dir
    except ImportError:
        summary["tools_error"] = "agentino not installed"
        return summary

    try:
        tools = discover_tools_from_dir(tools_dir)
        summary["tools"] = [_introspect_tool(t) for t in tools]
        summary["tools_dir"] = str(tools_dir)
    except Exception as exc:
        summary["tools_error"] = f"{type(exc).__name__}: {exc}"

    return summary


def inspect_workspace(workspace_yml: Path, agent_filter: list[str] | None = None) -> dict[str, Any]:
    """Return a full tenant catalog: {tenant, apps: [...]}."""
    cfg = yaml.safe_load(workspace_yml.read_text(encoding="utf-8")) or {}
    apps_raw = cfg.get("apps", {}) or {}

    catalog = {
        "tenant": cfg.get("name", workspace_yml.parent.name),
        "tenant_id": cfg.get("tenant_id", workspace_yml.parent.name),
        "config": str(workspace_yml),
        "providers": list((cfg.get("providers") or {}).keys()),
        "apps": [],
    }
    for app_id, app_cfg in apps_raw.items():
        if agent_filter and app_id not in agent_filter:
            continue
        if not isinstance(app_cfg, dict):
            continue
        if not app_cfg.get("enabled", True):
            continue
        catalog["apps"].append(inspect_app(workspace_yml, app_id, app_cfg))
    return catalog


def render_text(catalog: dict[str, Any]) -> str:
    """Pretty-print the catalog for terminal viewing."""
    lines: list[str] = []
    lines.append(f"{B}Tenant:{RESET} {catalog['tenant']} ({catalog['tenant_id']})")
    lines.append(f"{DIM}Config:{RESET} {catalog['config']}")
    lines.append(f"{DIM}Providers:{RESET} {', '.join(catalog['providers']) or '—'}")
    lines.append(f"{DIM}Apps:{RESET} {len(catalog['apps'])}")
    lines.append("")

    for app in catalog["apps"]:
        header = f"{G}{app['id']}{RESET}"
        if app["name"] and app["name"] != app["id"]:
            header += f"  ({app['name']})"
        if app["role"]:
            header += f"  — {app['role']}"
        lines.append(header)
        lines.append(f"  {DIM}type:{RESET}  {app['type']}")
        lines.append(f"  {DIM}model:{RESET} {app['model']}")
        if app.get("soul"):
            lines.append(f"  {DIM}soul:{RESET}  {app['soul']}")
        tools = app.get("tools") or []
        if app.get("tools_error"):
            lines.append(f"  {DIM}tools:{RESET} (error: {app['tools_error']})")
        elif not tools:
            lines.append(f"  {DIM}tools:{RESET} (none)")
        else:
            lines.append(f"  {DIM}tools:{RESET} ({len(tools)})")
            for t in tools:
                params_str = ", ".join(
                    f"{p['name']}: {p['type']}" + ("" if p["required"] else " = ?")
                    for p in t["params"]
                )
                lines.append(f"    • {t['name']}({params_str})")
                if t.get("description"):
                    desc = t["description"].splitlines()[0][:90]
                    lines.append(f"      {DIM}{desc}{RESET}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Inspect agent tool surface across a runspace tenant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("workspace_yml", help="Path to a tenant's workspace.yml")
    p.add_argument("--agent", "-a", help="Comma-separated subset of agent ids to inspect")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of pretty text")
    args = p.parse_args()

    workspace_yml = Path(args.workspace_yml).resolve()
    if not workspace_yml.exists():
        print(f"Error: workspace.yml not found: {workspace_yml}", file=sys.stderr)
        sys.exit(2)

    agent_filter = args.agent.split(",") if args.agent else None
    catalog = inspect_workspace(workspace_yml, agent_filter)

    if args.json:
        print(json.dumps(catalog, indent=2, default=str))
    else:
        print(render_text(catalog))


if __name__ == "__main__":
    main()
