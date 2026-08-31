"""Structural guards: nothing in `contracts/` or `protocols/` may import from runtime-coupled code paths."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # runspace/

# Modules / packages a contract must NEVER import. Adding entries here
# tightens the contract surface; never loosen.
FORBIDDEN_IMPORTS = {
    "agentino",  # the framework itself
    "agentino.core",
    "agentino.providers",
    "agentino.pipeline",
    "runspace.workspace.backend.gateway",  # runtime gateway
    "runspace.workspace.backend.app_registry",  # runtime registry
    "runspace.workspace.backend.activity_log",  # runtime telemetry
    "runspace.workspace.backend.messaging",  # runtime messaging
}

# Subtrees of runspace that MUST stay runtime-agnostic.
# `ingestion/` is the external-channel layer (Telegram webhook, pairing,
# context buffer). It's workspace-product code, not runtime-coupled —
# any runtime can host an agent that talks to a Telegram-paired chat.
# Lock this in: no `from agentino` may creep into ingestion/* over time.
GUARDED_TREES = ("contracts", "protocols/prompt", "ingestion")


def _collect_imports(py_file: Path) -> set[str]:
    """Return the set of module dotted-paths imported by a Python file.
    Includes both `import x.y` and `from x.y import z`."""
    src = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(py_file))
    except SyntaxError as e:
        pytest.fail(f"{py_file}: {e}")
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                out.add(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.add(node.module)
    return out


def _walk_python(path: Path):
    """Yield every Python source file under `path`, excluding tests, caches, dist."""
    for p in path.rglob("*.py"):
        parts = set(p.parts)
        if any(
            skip in parts
            for skip in (
                "__pycache__",
                "tests",
                "test",
                "dist",
                "build",
                ".venv",
                "venv",
            )
        ):
            continue
        yield p


@pytest.mark.parametrize("tree", GUARDED_TREES)
def test_no_runtime_imports_in_guarded_tree(tree):
    """Each file under `contracts/` or `protocols/prompt/` must not import
    from anything in FORBIDDEN_IMPORTS."""
    base = ROOT / tree
    if not base.exists():
        pytest.skip(f"{tree} not present in this checkout")

    offenders: list[tuple[Path, str]] = []
    for f in _walk_python(base):
        for imp in _collect_imports(f):
            for forbidden in FORBIDDEN_IMPORTS:
                if imp == forbidden or imp.startswith(forbidden + "."):
                    offenders.append((f.relative_to(ROOT), imp))
                    break

    assert not offenders, (
        f"Runtime imports leaked into the {tree}/ contract layer:\n  "
        + "\n  ".join(f"{f}: {imp}" for f, imp in offenders)
        + "\n\nMove the offending utility OUT of the contract layer or "
        f"refactor so the contract no longer needs it. The IP hedge "
        f"requires {tree}/ to be importable without the runtime."
    )


def test_contracts_package_imports_only_stdlib_and_pydantic_yaml():
    """All `contracts/*.py` (excluding tests) should be importable with
    just stdlib + pydantic + yaml. Anything else is a smell.

    This is a softer check than the forbidden-imports test — it surfaces
    new third-party deps that creep in over time."""
    base = ROOT / "contracts"
    if not base.exists():
        pytest.skip("contracts/ not present")

    allowed_third_party = {
        "pydantic",
        "yaml",
        # croniter is used by contracts/scheduling.py to compute next
        # cron run times. Pure-Python, no transport / runtime side. The
        # alternative — pushing next_run() out of the dataclass — would
        # force every consumer to import a runtime helper just to ask
        # "when is this scheduled next?". Keep it here.
        "croniter",
    }
    stdlib_known = {
        "__future__",
        "typing",
        "collections",
        "abc",
        "dataclasses",
        "enum",
        "pathlib",
        "re",
        "io",
        "ast",
        "json",
        "logging",
        "os",
        "sys",
        "warnings",
        "datetime",
        "functools",
        "itertools",
        "importlib",
        "contextlib",
        "copy",
        "tempfile",
        "time",
    }
    # Sibling module names within contracts/ (used by `from .X import Y`
    # which `ast.ImportFrom.module` resolves to bare `X`).
    sibling_modules = {p.stem for p in base.glob("*.py") if p.stem != "__init__"}

    suspects: list[tuple[Path, str]] = []
    for f in _walk_python(base):
        for imp in _collect_imports(f):
            top = imp.split(".")[0]
            if top in allowed_third_party or top in stdlib_known:
                continue
            # Same-package imports (`from .chat import …` → module="chat";
            # absolute `from contracts.chat import …` → module="runspace.contracts.chat").
            if top in ("contracts", "") or top in sibling_modules:
                continue
            suspects.append((f.relative_to(ROOT), imp))

    assert not suspects, (
        "contracts/ has unexpected third-party imports:\n  "
        + "\n  ".join(f"{f}: {imp}" for f, imp in suspects)
        + "\n\nIf this is legitimate, add the package to "
        "allowed_third_party in this test. Otherwise refactor the "
        "import out of the contract layer."
    )
