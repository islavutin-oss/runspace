"""Unified `runspace` CLI — dispatches to the standalone scripts/ files."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Map subcommand → underlying module in scripts/
COMMANDS: dict[str, tuple[str, str]] = {
    # subcommand:    (module_name, one-line description)
    "inspect": ("agents_inspect", "Catalog the tool surface of every agent in a tenant"),
    "smoke": ("agent_smoke", "Per-agent liveness probe (subprocess-isolated)"),
    "tools-usage": ("tools_usage", "Query the JSONL tool-usage telemetry log"),
    "tools-diff": ("tools_diff", "Snapshot + diff agent tool surfaces between deploys"),
    "render-docs": ("render_tool_docs", "Render Markdown docs per agent + tenant index"),
}


def _print_help() -> None:
    print("runspace — multi-tenant agent platform CLI\n")
    print("Usage: runspace <command> [args…]\n")
    print("Commands:")
    for cmd, (mod, desc) in COMMANDS.items():
        print(f"  {cmd:<14}  {desc}")
    print()
    print("Each subcommand accepts its own --help. Examples:")
    print("  runspace inspect tenants/acme/workspace.yml")
    print("  runspace smoke   tenants/acme/workspace.yml")
    print("  runspace tools-diff diff snapshots/acme.json tenants/acme/workspace.yml")


def _resolve_scripts_dir() -> Path:
    """scripts/ lives next to runspace_cli/ in the source tree.

    For an editable install (`pip install -e runspace`) this is the
    repo's `scripts/` dir. For a wheel install, the scripts files would
    need to be packaged separately — that's a follow-up if/when wheels
    become the primary install mode.
    """
    return Path(__file__).resolve().parent.parent / "scripts"


def main() -> None:
    """Entry point for the `runspace` console script."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_help()
        sys.exit(0 if len(sys.argv) >= 2 else 2)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"runspace: unknown command {cmd!r}\n", file=sys.stderr)
        _print_help()
        sys.exit(2)

    scripts_dir = _resolve_scripts_dir()
    if not scripts_dir.exists():
        print(f"runspace: scripts/ directory not found at {scripts_dir}.", file=sys.stderr)
        print(
            "If you installed runspace from a wheel, the standalone CLIs are not "
            "yet packaged — install in editable mode (`pip install -e runspace`) "
            "or invoke the script files directly.",
            file=sys.stderr,
        )
        sys.exit(3)
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    # Shift argv so the subcommand sees its own argv shape.
    # argv[0] becomes "runspace <cmd>" for nicer error/help banners.
    module_name, _ = COMMANDS[cmd]
    sys.argv = [f"runspace {cmd}"] + sys.argv[2:]

    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        print(f"runspace: failed to import {module_name}: {e}", file=sys.stderr)
        sys.exit(3)
    if not hasattr(module, "main"):
        print(f"runspace: {module_name}.py has no main() entry point", file=sys.stderr)
        sys.exit(3)
    module.main()
