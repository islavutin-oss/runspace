"""AgentRuntime contract — the seam each runtime implements."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Attachment:
    """File attachment carried into a turn (e.g. a scanned invoice)."""

    file_id: str
    original_name: str
    mime_type: str
    size_bytes: int


@dataclass
class AgentTurnDelta:
    """One streaming chunk from a runtime."""

    text: str = ""
    is_final: bool = False
    tool_call: dict | None = None


@dataclass
class AgentTurnResult:
    """Final result of one agent turn — runtime-agnostic."""

    text: str
    runtime: str  # "agentino" | "openclaw" | ...
    tool_calls: list[dict] = field(default_factory=list)
    runtime_session_id: str | None = None
    duration_ms: int = 0
    cost_usd: float | None = None
    error: str | None = None


@runtime_checkable
class AgentRuntime(Protocol):
    """Every runtime that can serve an agent turn implements this.

    The dispatcher (`AgentRuntimeRouter` in acme) holds a map
    `{name: AgentRuntime}` and calls `run_turn(...)` on whichever
    runtime the tenant's `workspace.yml apps.<id>.type:` resolves to.
    """

    name: str  # canonical id: "agentino" | "openclaw"

    async def run_turn(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        session_key: str,
        message: str,
        attachments: list[Attachment] | None = None,
        sender_id: str | None = None,
        channel: str | None = None,
    ) -> AgentTurnResult: ...

    async def stream_turn(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        session_key: str,
        message: str,
        attachments: list[Attachment] | None = None,
        sender_id: str | None = None,
        channel: str | None = None,
    ) -> AsyncIterator[AgentTurnDelta]:
        """Optional. Subprocess-based runtimes may yield a single final delta."""
        ...


__all__ = [
    "AgentRuntime",
    "AgentTurnResult",
    "AgentTurnDelta",
    "Attachment",
]
