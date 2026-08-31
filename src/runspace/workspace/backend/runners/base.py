"""Generic runner abstraction — replaces project-specific orchestrators."""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..sessions import SessionWriter  # type: ignore  # noqa: F401


@dataclass
class TrialResult:
    """Outcome of one trial. Same shape across runners so the dashboard
    can render any of them with one component."""

    task_id: str
    score: float  # 0.0–1.0
    score_detail: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    final_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    # Optional cost / business value — feeds the cost ledger + business
    # KPI views in the dashboard. Runners that don't track these leave
    # them at 0; presence is opt-in.
    cost_cents: float = 0.0
    business_value: float = 0.0


@dataclass
class Trial:
    """One unit of work scheduled by a runner. The runner calls `execute()`
    when the dashboard says "go" — that's where the actual agent invocation
    lives. Letting the runner produce trial *closures* (rather than running
    everything synchronously) lets the dashboard parallelize, cancel,
    inspect, etc."""

    task_id: str
    instruction: str
    execute: Callable[[], Awaitable[TrialResult]]
    # Free-form metadata: variant name (A/B), customer id (replay),
    # workload row index, etc. Stored in the trial result's extra.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunnerContext:
    """Everything a runner needs from the dashboard host. Constructed
    once per run, passed to `Runner.iter_trials()`."""

    project: str  # name in PROJECTS registry
    project_root: Path  # absolute path to project workspace
    session: Any  # SessionWriter — open + ready to receive write_trace/write_trial_result
    settings: dict[str, Any]  # current settings_store snapshot
    name: str | None = None  # run label
    options: dict[str, Any] = field(default_factory=dict)  # per-run knobs (filters, parallelism)


class Runner(abc.ABC):
    """Subclass interface. Concrete runners (workload, harness, ab) live
    next to this file."""

    name: str = ""
    type: str = ""
    description: str = ""

    @abc.abstractmethod
    def iter_trials(self, ctx: RunnerContext) -> AsyncIterator[Trial]:
        """Yield Trial closures. Implementations are async generators."""
        raise NotImplementedError

    def expected_trial_count(self, ctx: RunnerContext) -> int | None:
        """Best-effort count for progress UI. None = unknown until iter_trials runs."""
        return None
