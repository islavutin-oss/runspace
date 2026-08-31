"""AgentTool contract — runtime-agnostic tool interface."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentTool(Protocol):
    """The minimal contract a runtime's "tool" must satisfy.

    Mandatory fields:
      - `name`: unique identifier the LLM uses to call the tool.
      - `description`: human + model-readable summary; the LLM picks
        tools by reading this.
      - `parameters`: JSON-Schema dict (or compatible) describing input
        args. Both agentino's pydantic-ish dict and openclaw's TypeBox
        schemas serialize to JSON Schema.
      - `fn` (or callable interface): the actual logic. May be sync or
        async. Implementations may also expose `execute(...)` (openclaw
        style) — runtimes-side adapters bridge whichever shape they use
        to a common call: `tool(args) -> result`.

    Optional behavioural metadata:
      - `is_read_only`: marks the tool as side-effect-free (safe for
        parallel execution, retry, dry-run).
      - `timeout`: per-call timeout in seconds.

    Notes:
      - The contract intentionally does NOT prescribe permissions / gates
        — those live in `agents.gates` (workspace.yml) and are enforced
        at the runtime layer, not per-tool.
      - The contract uses `Any` for the result on purpose. Tools return
        strings, dicts, structured envelopes (`{content: [...]}`) — each
        runtime's caller normalises before passing to the LLM. Pinning a
        type here would force runtimes to converge on a single
        normalisation, which is premature.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]]
    is_read_only: bool
    timeout: float | None


__all__ = ["AgentTool"]
