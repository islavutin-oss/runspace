"""End-to-end agent flow tests — agent → tool → Store → agent."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from runspace.protocols.store import InMemoryStore

# Skip the whole module if agentino isn't on PYTHONPATH (sandbox-mode CI fork).
agentino = pytest.importorskip("agentino")
from agentino import Agent, Message, ToolCall, Usage, tool  # noqa: E402
from agentino.core.llm import LLMResponse  # noqa: E402


# ── LLM stub helpers ────────────────────────────────────────────────────
def _text(content: str) -> LLMResponse:
    return LLMResponse(
        message=Message(role="assistant", content=content),
        usage=Usage(prompt_tokens=10, completion_tokens=5),
    )


def _call(tool_name: str, args: dict, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        message=Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id=call_id, name=tool_name, arguments=args)],
        ),
        usage=Usage(prompt_tokens=10, completion_tokens=5),
    )


def _llm_returns(*responses: LLMResponse) -> AsyncMock:
    """Create an AsyncMock that returns each response in sequence."""
    return AsyncMock(side_effect=list(responses))


# ── Synthetic tools that use Store (close to real Ada/lemana flow) ─────
@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def tools(store):
    """Two store-backed tools: list_invoices + mark_paid. Mirrors the
    ada pattern."""

    @tool(is_read_only=True)
    def list_invoices(status: str = "") -> list[dict]:
        """List invoices. Optional status filter."""
        if status:
            return store.query("invoices", status=status)
        return store.list("invoices")

    @tool
    def mark_paid(invoice_id: str) -> dict:
        """Mark an invoice as paid by id."""
        out = store.update("invoices", invoice_id, status="paid")
        if out is None:
            return {"error": f"invoice {invoice_id} not found"}
        return {"ok": True, "id": invoice_id}

    return [list_invoices, mark_paid]


# ── Tests ───────────────────────────────────────────────────────────────
class TestSingleToolFlow:
    """Simplest case: agent calls one tool then replies."""

    @pytest.mark.asyncio
    async def test_list_then_summarize(self, store, tools):
        """LLM calls list_invoices, sees results, summarizes."""
        store.save("invoices", {"id": "i01", "status": "pending", "amount": 100})
        store.save("invoices", {"id": "i02", "status": "paid", "amount": 50})

        agent = Agent(model="test", tools=tools)
        agent._llm.chat = _llm_returns(
            _call("list_invoices", {}),
            _text("You have 2 invoices on file."),
        )
        result = await agent.run("What invoices do I have?")
        assert result == "You have 2 invoices on file."
        # Both invoices still present (read-only tool didn't change anything)
        assert len(store.list("invoices")) == 2


class TestToolMutatesStore:
    """Critical case: tool writes to Store, agent confirms in next turn."""

    @pytest.mark.asyncio
    async def test_mark_paid_persists(self, store, tools):
        store.save("invoices", {"id": "i01", "status": "pending"})

        agent = Agent(model="test", tools=tools)
        agent._llm.chat = _llm_returns(
            _call("mark_paid", {"invoice_id": "i01"}),
            _text("i01 marked paid."),
        )
        await agent.run("Mark i01 paid")
        # The Store reflects the mutation
        assert store.get("invoices", "i01")["status"] == "paid"


class TestMultiStepFlow:
    """Real Ada-flow: list → identify → mark → confirm."""

    @pytest.mark.asyncio
    async def test_list_then_mark_then_summarize(self, store, tools):
        store.save("invoices", {"id": "i01", "status": "pending", "supplier": "X"})
        store.save("invoices", {"id": "i02", "status": "pending", "supplier": "Y"})

        agent = Agent(model="test", tools=tools)
        agent._llm.chat = _llm_returns(
            _call("list_invoices", {"status": "pending"}, "c1"),
            _call("mark_paid", {"invoice_id": "i01"}, "c2"),
            _call("list_invoices", {"status": "paid"}, "c3"),
            _text("Marked i01 paid. 1 invoice now in paid status."),
        )
        result = await agent.run("Mark the X invoice paid")
        assert "paid" in result.lower()
        # Verify the chain actually executed: Store state matches expectation
        assert store.get("invoices", "i01")["status"] == "paid"
        assert store.get("invoices", "i02")["status"] == "pending"


class TestToolErrorPropagation:
    """If a tool returns an error dict, the agent's next turn must see it
    and decide what to do — not crash, not silently succeed."""

    @pytest.mark.asyncio
    async def test_unknown_id_error_routed_back_to_llm(self, store, tools):
        agent = Agent(model="test", tools=tools)
        agent._llm.chat = _llm_returns(
            _call("mark_paid", {"invoice_id": "nope"}, "c1"),
            _text("I couldn't find invoice 'nope' — please check the id."),
        )
        result = await agent.run("Mark nope paid")
        assert "couldn't find" in result.lower() or "not found" in result.lower()
        # Store unchanged
        assert store.list("invoices") == []


class TestStoreVisibilityAcrossTurns:
    """Mutation in turn 1 must be visible to a list-call in turn 2 — same
    Store instance throughout. This is exactly where mock-based testing
    breaks down (mocks don't reflect mutations); contract testing here
    catches it."""

    @pytest.mark.asyncio
    async def test_write_then_read_sees_change(self, store, tools):
        agent = Agent(model="test", tools=tools)
        # Turn 1: list (sees nothing)
        # Turn 2: …but the user's "imagine I created one" doesn't touch Store, so we
        # simulate by writing in setup then having the LLM consult it twice.
        store.save("invoices", {"id": "i01", "status": "pending"})

        agent._llm.chat = _llm_returns(
            _call("list_invoices", {"status": "pending"}, "c1"),
            _call("mark_paid", {"invoice_id": "i01"}, "c2"),
            _call("list_invoices", {"status": "pending"}, "c3"),
            _text("Initially 1 pending; now 0."),
        )
        result = await agent.run("audit pending and pay i01")
        assert "0" in result
        assert store.get("invoices", "i01")["status"] == "paid"


class TestConsecutiveAgentRuns:
    """Two `agent.run()` calls share the same Store — like a real chat
    where the user sends multiple messages."""

    @pytest.mark.asyncio
    async def test_state_persists_between_runs(self, store, tools):
        store.save("invoices", {"id": "i01", "status": "pending"})

        agent = Agent(model="test", tools=tools)
        agent._llm.chat = _llm_returns(
            # First run
            _call("mark_paid", {"invoice_id": "i01"}, "c1"),
            _text("done."),
            # Second run — Store must reflect first run's mutation
            _call("list_invoices", {"status": "paid"}, "c2"),
            _text("1 paid invoice."),
        )
        await agent.run("pay i01")
        result = await agent.run("how many paid?")
        assert "1" in result
