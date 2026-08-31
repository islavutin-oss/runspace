"""AgentTool Protocol tests — pin the structural contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from runspace.contracts import AgentTool


@dataclass
class _MinimalTool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Any
    is_read_only: bool = False
    timeout: float | None = None


def test_minimal_dataclass_satisfies_contract():
    t = _MinimalTool(
        name="due_today",
        description="invoices due today",
        parameters={"type": "object"},
        fn=lambda: "ok",
    )
    assert isinstance(t, AgentTool)


def test_object_missing_field_fails_isinstance():
    class BadTool:
        name = "x"
        description = "y"
        parameters: dict = {}

        def fn(self):
            return None

        # missing is_read_only and timeout

    bt = BadTool()
    assert not isinstance(bt, AgentTool)


def test_agentino_tool_satisfies_contract():
    """The real agentino Tool dataclass must satisfy the contract.
    If this breaks, agentino's tool definition has drifted from the
    runtime-agnostic shape — fix the contract or fix agentino.
    Skipped when agentino isn't importable."""
    try:
        from agentino.core.tool import tool as agentino_tool
    except Exception:
        pytest.skip("agentino framework not importable in this test run")

    @agentino_tool
    def example(x: int) -> str:
        """Sum + return as text."""
        return str(x)

    assert isinstance(example, AgentTool), (
        "agentino's @tool-decorated Tool no longer satisfies AgentTool. "
        "Check if any contract field was renamed/dropped on either side."
    )


def test_no_agentino_import_in_contract_module():
    """The contract module must not pull in the agentino framework.
    This test reads the source file and asserts no `import agentino` etc.
    Catches future regressions where someone adds a convenience import."""
    from runspace.contracts import tool as m

    src = open(m.__file__).read()
    forbidden = ["import agentino", "from agentino"]
    for f in forbidden:
        assert f not in src, (
            f"contracts/tool.py imports `{f}` — that re-couples the "
            f"contract layer to the agentino runtime. Move the import "
            f"or remove it."
        )
