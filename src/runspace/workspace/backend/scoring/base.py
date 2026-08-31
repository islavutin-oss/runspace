"""Scorer abstraction — pluggable per-project quality measurement."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScorerInput:
    """Input to scorer.judge() — everything we know about the trial outcome."""

    instruction: str
    final_text: str
    tools_used: list[str] = field(default_factory=list)
    grounding_refs: list[str] = field(default_factory=list)
    expected: Any = None  # ground truth from workload row
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScorerVerdict:
    score: float  # 0.0–1.0
    score_detail: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


class Scorer(abc.ABC):
    """Subclass to plug a custom scoring function."""

    name: str = ""

    @abc.abstractmethod
    async def judge(self, inp: ScorerInput) -> ScorerVerdict:
        raise NotImplementedError
