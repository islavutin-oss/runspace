"""AgentRuntime Protocol — pin the dispatcher seam."""

from __future__ import annotations

import pytest

from runspace.contracts import (
    AgentRuntime,
    AgentTurnDelta,
    AgentTurnResult,
    Attachment,
)


def test_attachment_is_frozen_dataclass():
    a = Attachment(
        file_id="f1", original_name="x.pdf", mime_type="application/pdf", size_bytes=1234
    )
    assert a.file_id == "f1"
    with pytest.raises(Exception):  # FrozenInstanceError
        a.file_id = "mutated"  # type: ignore[misc]


def test_agent_turn_result_default_collections():
    r = AgentTurnResult(text="ok", runtime="agentino")
    assert r.tool_calls == []
    assert r.runtime_session_id is None
    assert r.error is None
    assert r.cost_usd is None


def test_minimal_runtime_satisfies_protocol():
    """Bare async runtime with the right method shape passes isinstance."""

    class _R:
        name = "test"

        async def run_turn(self, **kw) -> AgentTurnResult:
            return AgentTurnResult(text="x", runtime=self.name)

        async def stream_turn(self, **kw):
            yield AgentTurnDelta(text="x", is_final=True)

    rt = _R()
    assert isinstance(rt, AgentRuntime)


def test_runtime_missing_name_fails_isinstance():
    class _R:
        async def run_turn(self, **kw): ...
        async def stream_turn(self, **kw):
            if False:
                yield None  # pragma: no cover

    assert not isinstance(_R(), AgentRuntime)
