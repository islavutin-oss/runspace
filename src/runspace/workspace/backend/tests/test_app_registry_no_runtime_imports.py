"""Structural guard for Class C of the runtime-decoupling redesign."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REGISTRY = Path(__file__).resolve().parents[1] / "app_registry.py"


def _toplevel_and_inline_imports(py_file: Path):
    """Yield every import dotted-name (top-level OR inside a function)."""
    src = py_file.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(py_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                yield n.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module


def test_app_registry_does_not_import_agentino_framework():
    """No `agentino` (or `agentino.X`) imports — anywhere. The IP hedge
    requires runspace to be installable without the agentino runtime."""
    if not REGISTRY.exists():
        pytest.skip("app_registry.py not present")

    offenders = []
    for imp in _toplevel_and_inline_imports(REGISTRY):
        # Allow the runtime adapter dotted path; that's our seam.
        # The forbidden case is `agentino` (the framework root) and
        # any of its subpackages.
        top = imp.split(".")[0]
        if top == "agentino":
            offenders.append(imp)

    assert not offenders, (
        "app_registry.py imports from the agentino runtime:\n  "
        + "\n  ".join(sorted(set(offenders)))
        + "\n\nMove the agentino-coupled code into "
        "workspace/backend/runtimes/agentino.py and have the registry "
        "delegate via a thin shim. Class C of the runtime-decoupling "
        "redesign required this seam."
    )


def test_runtime_adapter_module_exists_and_owns_agentino_imports():
    """The runtimes/agentino.py module must exist (it's where the
    agentino imports got moved to) and must contain the agentino
    framework imports we care about (Agent, Event, GateManager, etc).

    This is the inverse guard: if someone deletes the runtime adapter
    or empties it out, the registry would have nowhere to delegate to."""
    runtime = REGISTRY.parent / "runtimes" / "agentino.py"
    assert runtime.exists(), (
        "workspace/backend/runtimes/agentino.py was removed. The "
        "registry delegates here for agentino-typed apps. Restore it "
        "or update the seam everywhere."
    )
    src = runtime.read_text(encoding="utf-8")
    # Sanity: the agentino imports are still in the right place.
    for needle in ("from agentino import", "from agentino.core", "from agentino.safety.gates"):
        assert needle in src, (
            f"Expected `{needle}` somewhere in runtimes/agentino.py — "
            f"the agentino-coupled code seems to have been moved out. "
            f"That's fine if you re-routed it, but update this test."
        )


def test_registry_delegates_to_runtime_adapter():
    """The registry's chat dispatch must reach into runtimes/agentino.py
    for `type: agentino` apps. Any future routing (e.g. a Protocol-based
    AgentRuntime registry) needs to keep this seam, otherwise we lose
    the IP-hedge boundary."""
    src = REGISTRY.read_text(encoding="utf-8")
    assert "from .runtimes import agentino" in src, (
        "AppRegistry no longer delegates to runtimes/agentino. The "
        "agentino-runtime dispatch must go through that adapter so "
        "app_registry.py stays free of `from agentino` imports."
    )
