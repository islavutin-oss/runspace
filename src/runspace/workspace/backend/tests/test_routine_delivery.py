"""A routine that produces text must deliver it."""

from __future__ import annotations

import ast
from pathlib import Path

_GATEWAY = Path(__file__).resolve().parents[1] / "gateway.py"


def _run_routine_source() -> str:
    """The body of the manual-run endpoint."""
    tree = ast.parse(_GATEWAY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_routine":
            return ast.get_source_segment(_GATEWAY.read_text(encoding="utf-8"), node) or ""
    raise AssertionError("run_routine endpoint not found in gateway.py")


def test_delivery_does_not_hardcode_a_backend():
    src = _run_routine_source()
    assert "MessagingService(" not in src, (
        "routine delivery constructs a messaging backend directly; it must use "
        "the one the gateway already resolved, or it posts nothing on any "
        "deployment that is not Supabase"
    )


def test_delivery_uses_the_resolved_messaging_service():
    src = _run_routine_source()
    assert "_require_messaging()" in src or "self._messaging" in src


def test_no_other_route_constructs_its_own_backend():
    """The same mistake anywhere else has the same silent shape."""
    source = _GATEWAY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(source, node) or ""
        # from_config legitimately builds the backend once, at construction.
        if node.name == "from_config":
            continue
        if "MessagingService(" in body:
            offenders.append(node.name)
    assert not offenders, (
        f"these build their own messaging backend instead of using the resolved one: {offenders}"
    )
