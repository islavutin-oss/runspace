"""An agent's tools, callable from a shell.

Why this exists
---------------

Most runtimes are handed a `tools` array over an OpenAI-compatible API. The
CLI-driven ones are not: they drive their own loop and reach the outside world
through their own tools, Bash chief among them. So for an agent to keep its
tools when it moves onto a CLI harness, those tools have to be reachable from a
shell.

They are already written once, as `@tool`-decorated coroutines, and they stay
that way. This is a dispatcher, not a second copy: it loads the same modules the
in-process runtime loads, through the same `discover_tools_from_dir`, and calls
the same `Tool.execute`. A tool added for the in-process path is callable here
the moment it exists, with no registration step — which is the only version of
this worth having, because two lists of tools drift the week after you write
them.

Usage
-----

    python -m runspace.workspace.backend.runtimes.tools_cli list
    python -m runspace.workspace.backend.runtimes.tools_cli schema <tool>
    python -m runspace.workspace.backend.runtimes.tools_cli call <tool> '{"k": "v"}'

`call` prints whatever the tool returns on stdout and exits 0; it prints the
error and exits 1 if the tool raises. That is the contract a shell wants — a
harness reading stdout should not have to parse success out of prose.

Configuration
-------------

`RUNSPACE_TOOL_DIRS` is a comma-separated list of tool trees to load. Entries
may be absolute, or relative to `RUNSPACE_TOOLS_ROOT` (default: the working
directory). Nothing is hardcoded here: a dispatcher that names one deployment's
directories is a dispatcher that works on the machine that wrote it and nowhere
else.

This module is deliberately not imported by the registry or any adapter. It is
an entry point a workspace's `tools` script execs, so the one import of the
agent framework stays inside a function and out of the import graph.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_DIRS_ENV = "RUNSPACE_TOOL_DIRS"
_CONTEXT_ENV = "RUNSPACE_TOOL_CONTEXT"
_ROOT_ENV = "RUNSPACE_TOOLS_ROOT"


def _root() -> Path:
    return Path(os.environ.get(_ROOT_ENV) or os.getcwd()).resolve()


def _tool_dirs() -> tuple[str, ...]:
    raw = (os.environ.get(_DIRS_ENV) or "").strip()
    return tuple(d.strip() for d in raw.split(",") if d.strip())


def _agentino_api():
    """`discover_tools_from_dir` for whichever agent framework layout is installed.

    The published package exports the decorator as `agentino.tool` and keeps the
    directory loader in `agentino.config.tools_yaml`. Installs predating that
    layout have a flat `agentino/tool.py` and `agentino/config_tools.py`, and the
    two cannot be mixed: each loader collects objects that are instances of *its*
    `Tool` class, so pairing one layout's loader with the other's decorator
    silently discovers nothing at all — no error, just an agent that has no
    tools and answers from memory instead.
    """
    import agentino

    decorator = getattr(agentino, "tool", None)
    if callable(decorator):  # published layout
        from agentino.config.tools_yaml import discover_tools_from_dir

        return discover_tools_from_dir, decorator

    # Pre-move layout: `agentino.tool` is a submodule, which also means
    # importing any other submodule first rebinds the name away from the
    # decorator that `__init__.py` exported. Tool files that open with
    # `from agentino import tool` would then all fail with "'module' object is
    # not callable". Put it back.
    from agentino.config_tools import discover_tools_from_dir
    from agentino.tool import tool as decorator

    agentino.tool = decorator
    return discover_tools_from_dir, decorator


def seed_context() -> dict:
    """Carry the caller's context across the process boundary.

    In-process, tools read ambient context — `get_context("tenant_id")` — from
    a ContextVar the server sets per request. A subprocess starts with that
    empty, so a tool that was correct on one runtime raises on this one. The
    failure is loud where the tool checks ("refusing to fall back") and silent
    where it does not, which is worse: the same tool quietly answers for the
    wrong tenant.

    `RUNSPACE_TOOL_CONTEXT` is a JSON object the shim carries. Seeding it here
    rather than teaching the framework about environment variables keeps the
    process boundary a concern of the thing that crosses it.
    """
    raw = (os.environ.get(_CONTEXT_ENV) or "").strip()
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"warning: {_CONTEXT_ENV} is not valid JSON: {e}", file=sys.stderr)
        return {}
    if not isinstance(values, dict) or not values:
        return {}

    try:
        from agentino.core.context import set_context
    except ImportError:
        return {}
    set_context(**values)
    return values


def load_tools() -> dict:
    """Every tool in every declared tree, keyed by name.

    Nothing is scoped here. Which agent may call which tool is a gate, and it
    is enforced where the other gates live — the per-app allowlist the harness
    applies before the command runs. Scoping here as well would put the same
    rule in two places and let them disagree.
    """
    discover_tools_from_dir, _ = _agentino_api()
    seed_context()
    root = _root()

    found: dict = {}
    for rel in _tool_dirs():
        path = Path(rel) if os.path.isabs(rel) else root / rel
        if not path.is_dir():
            print(f"warning: no such tool directory: {path}", file=sys.stderr)
            continue
        for tool in discover_tools_from_dir(path):
            # First definition wins. A duplicate name across trees is a bug in
            # the workspace, not something to resolve silently by load order —
            # say so rather than letting the harness call whichever won.
            if tool.name in found:
                print(
                    f"warning: duplicate tool {tool.name!r} in {rel}, keeping the first",
                    file=sys.stderr,
                )
                continue
            found[tool.name] = tool
    return found


def _cmd_list(tools: dict) -> int:
    width = max((len(n) for n in tools), default=0)
    for name in sorted(tools):
        summary = (tools[name].description or "").strip().splitlines()
        print(f"{name:<{width}}  {summary[0] if summary else ''}")
    return 0


def _cmd_schema(tools: dict, name: str) -> int:
    tool = tools.get(name)
    if tool is None:
        print(f"no such tool: {name}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
            indent=2,
        )
    )
    return 0


def _cmd_call(tools: dict, name: str, raw_args: str) -> int:
    tool = tools.get(name)
    if tool is None:
        print(f"no such tool: {name}. Run `list` to see what exists.", file=sys.stderr)
        return 1
    try:
        arguments = json.loads(raw_args) if raw_args.strip() else {}
    except json.JSONDecodeError as e:
        print(f"arguments must be a JSON object: {e}", file=sys.stderr)
        return 1
    if not isinstance(arguments, dict):
        print(
            f"arguments must be a JSON object, not a {type(arguments).__name__}",
            file=sys.stderr,
        )
        return 1

    # Before the tool runs, not after: asyncio.run gives the task a *copy* of
    # the context, so a ContextVar first set inside the task is discarded when
    # it ends. Installing the list out here means the copy references the same
    # object, and the block the tool registered is still there to splice.
    _begin_turn()

    try:
        result = asyncio.run(tool.execute(arguments))
    except Exception as e:  # the tool's own failure, reported as a failure
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # A tool may return a result object rather than a string; both carry text.
    text = result if isinstance(result, str) else getattr(result, "content", result)
    print(_restore_blocks(text) if isinstance(text, str) else text)
    return 0


def _begin_turn() -> None:
    """Install this turn's UI-block list before any tool can register one."""
    try:
        from .._mcp_ui import begin_turn

        begin_turn()
    except ImportError:
        pass


def _restore_blocks(text: str) -> str:
    """Splice this turn's canonical UI blocks into the output.

    A tool that returns a chart registers the real block and emits a
    placeholder — `{"$mcpui": 0}` — for the caller to splice. In-process that
    works: the block sits in a ContextVar the caller shares. Across a process
    boundary it cannot, and the caller receives a reference to something that
    died with the subprocess: every tool-rendered chart reached the model as
    `{"$mcpui": 0}`, and agents correctly reported they had no chart to show.

    This process owns both halves, so it splices before printing.
    """
    try:
        from .._mcp_ui import restore_mcp_ui_blocks
    except ImportError:
        return text
    try:
        return restore_mcp_ui_blocks(text)
    except Exception as e:  # never lose the answer over its formatting
        print(f"warning: could not restore UI blocks: {e}", file=sys.stderr)
        return text


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print((__doc__ or "").strip())
        return 0

    command, rest = argv[0], argv[1:]

    if command == "list":
        return _cmd_list(load_tools())
    if command == "schema":
        if not rest:
            print("usage: schema <tool>", file=sys.stderr)
            return 1
        return _cmd_schema(load_tools(), rest[0])
    if command == "call":
        if not rest:
            print("usage: call <tool> ['<json>']", file=sys.stderr)
            return 1
        return _cmd_call(load_tools(), rest[0], rest[1] if len(rest) > 1 else "")

    print(f"unknown command: {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
