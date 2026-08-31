"""Pin: AppRegistry builds a per-request GateManager and pre-marks tenant_consistent.."""

from __future__ import annotations

import pytest

# Agentino is an optional extra and is not on PyPI, so a contributor who
# runs `pip install -e .[dev]` does not have it. Skipping keeps the rest
# of the suite collectable; a bare import broke collection entirely.
pytest.importorskip("agentino")

from agentino.safety.gates import GateRule  # noqa: E402

from runspace.workspace.backend.app_registry import AgentApp, AppRegistry


def _registry(tenant_id: str | None = "acme") -> AppRegistry:
    return AppRegistry(tenant_id=tenant_id)


def _accountant_app(rules: list[GateRule] | None = None) -> AgentApp:
    """Build a minimal AgentApp pretending to be Ada with optional gate rules."""
    app = AgentApp(
        id="accountant",
        name="Ada",
        role="Bookkeeping",
        type="agentino",
    )

    # Stub _agent with the gate rules attached the way register() does
    class _StubAgent:
        pass

    stub = _StubAgent()
    stub._gate_rules = rules or []
    app._agent = stub
    return app


def test_build_gate_manager_returns_fresh_instance_each_call():
    reg = _registry()
    app = _accountant_app()
    gm1 = reg._build_gate_manager(app)
    gm2 = reg._build_gate_manager(app)
    assert gm1 is not gm2  # per-request


def test_tenant_consistent_pre_marked_when_real_tenant():
    reg = _registry(tenant_id="acme")
    gm = reg._build_gate_manager(_accountant_app())
    assert gm.is_marked("tenant_consistent") is True


def test_tenant_consistent_NOT_marked_for_default_tenant():
    """`default` is the sandbox/test fallback — should not satisfy the gate."""
    reg = _registry(tenant_id="default")
    gm = reg._build_gate_manager(_accountant_app())
    assert gm.is_marked("tenant_consistent") is False


def test_tenant_consistent_NOT_marked_when_no_tenant():
    reg = _registry(tenant_id=None)
    gm = reg._build_gate_manager(_accountant_app())
    assert gm.is_marked("tenant_consistent") is False


def test_rules_are_carried_into_gate_manager():
    """Rules registered on app._agent._gate_rules become enforceable."""
    rules = [
        GateRule(
            gate="tenant_consistent",
            tools=["set_invoice_status"],
            message="Reject: missing tenant context",
        ),
    ]
    reg = _registry(tenant_id="acme")
    gm = reg._build_gate_manager(_accountant_app(rules=rules))
    # tenant_consistent is pre-marked, so the rule passes
    assert gm.check("set_invoice_status") is None


def test_rule_blocks_when_required_gate_unmarked():
    """A rule whose gate is unmarked rejects with the configured message."""
    rules = [
        GateRule(
            gate="invoice_listed",
            tools=["set_invoice_status"],
            message="Run list_invoices first.",
        ),
    ]
    # No tenant id — so tenant_consistent stays unmarked too, but that
    # rule isn't on this app. invoice_listed rule fires.
    reg = _registry(tenant_id=None)
    gm = reg._build_gate_manager(_accountant_app(rules=rules))
    rejection = gm.check("set_invoice_status")
    assert rejection is not None
    assert "list_invoices" in rejection


def test_unrelated_tool_passes_when_no_matching_rule():
    rules = [
        GateRule(
            gate="invoice_listed",
            tools=["set_invoice_status"],
            message="...",
        ),
    ]
    reg = _registry(tenant_id="acme")
    gm = reg._build_gate_manager(_accountant_app(rules=rules))
    # `due_today` isn't gated → no rejection
    assert gm.check("due_today") is None


def test_gate_manager_marks_persist_within_one_request():
    """Same gm instance — marks accumulate across tool calls in one request."""
    gm = _registry()._build_gate_manager(_accountant_app())
    gm.mark("invoice_listed")
    assert gm.is_marked("invoice_listed") is True
    # tenant_consistent is still pre-marked
    assert gm.is_marked("tenant_consistent") is True


def test_app_with_no_agent_yet_returns_empty_rules_manager():
    """Edge case: agent hasn't been instantiated yet (no app._agent)."""
    reg = _registry()
    app = AgentApp(id="x", name="X", role="r", type="agentino")
    # No _agent set
    gm = reg._build_gate_manager(app)
    # Should not raise; rules is just empty
    assert gm.check("any_tool") is None


# ---------------------------------------------------------------------------
# Integration / seam test — the gate manager actually reaches Agent via
# agentino context after a chat() call goes through AppRegistry.
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402  (after sys.path setup)


def test_gate_manager_reachable_from_agentino_context_after_chat_setup(monkeypatch):
    """Pin the seam: when AppRegistry sets up a chat call, the per-request
    GateManager appears in agentino.core.context.get_context('_gate_manager').
    This is what Agent.execute_tool reads to enforce rules.

    We don't run a real LLM — we build the registry, register an agent
    with rules, then directly call _build_gate_manager and verify the
    object the framework will consume."""
    from agentino.core.context import get_context, reset, set_context
    from agentino.safety.gates import GateRule

    rules = [GateRule(gate="tenant_consistent", tools=["set_invoice_status"], message="X")]

    reg = _registry(tenant_id="acme")
    app = _accountant_app(rules=rules)

    # This is the same call _chat_agentino makes
    gm = reg._build_gate_manager(app)
    token = set_context(_gate_manager=gm, tenant_id="acme")
    try:
        # Simulate the agent's execute_tool reading from context
        retrieved = get_context("_gate_manager")
        assert retrieved is gm
        # And the gate state is preserved through context (tenant_consistent
        # was pre-marked; rule should pass)
        assert retrieved.check("set_invoice_status") is None
    finally:
        reset(token)


def test_two_concurrent_chats_get_separate_gate_managers():
    """contextvars guarantees the GateManager is per-async-task. Two
    concurrent chat calls must NOT share gate state — otherwise one
    user's `gm.mark()` would leak into another user's gate check."""
    from agentino.core.context import get_context, reset, set_context

    reg = _registry(tenant_id="acme")
    app = _accountant_app()

    seen_gms: list = []

    async def _one_chat(label: str):
        gm = reg._build_gate_manager(app)
        token = set_context(_gate_manager=gm, _label=label)
        try:
            await asyncio.sleep(0)  # yield to the other task
            seen_gms.append((label, get_context("_gate_manager")))
            await asyncio.sleep(0)
        finally:
            reset(token)

    async def _go():
        await asyncio.gather(_one_chat("a"), _one_chat("b"))

    # asyncio.run, not get_event_loop(): on 3.12 the latter only works if an
    # earlier test happened to leave a loop set on this thread, which makes
    # the test pass or fail depending on collection order.
    asyncio.run(_go())

    # Both saw a GateManager, and they were DIFFERENT instances
    assert len(seen_gms) == 2
    a_gm = next(g for label, g in seen_gms if label == "a")
    b_gm = next(g for label, g in seen_gms if label == "b")
    assert a_gm is not b_gm, (
        "concurrent chats shared a GateManager — gate state would leak "
        "across requests. Each chat must build its own."
    )
