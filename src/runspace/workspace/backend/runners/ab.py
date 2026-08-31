"""A/B runner — runs the same workload across N variants and reports per-variant scores."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .base import Runner, RunnerContext, Trial, TrialResult
from .workload import WorkloadConfig, WorkloadRunner

log = logging.getLogger(__name__)


@dataclass
class Variant:
    name: str
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class ABConfig:
    name: str
    file: str  # workload path
    app_id: str
    variants: list[Variant]
    scorer: str | None = None
    description: str = ""


class ABRunner(Runner):
    type = "ab"

    def __init__(self, cfg: ABConfig, registry: Any, scorer: Any | None = None):
        self.name = cfg.name
        self.description = cfg.description
        self.cfg = cfg
        self.registry = registry
        self.scorer = scorer

    def expected_trial_count(self, ctx: RunnerContext) -> int | None:
        # one workload × N variants
        try:
            sub = WorkloadRunner(
                WorkloadConfig(
                    name=self.cfg.name,
                    file=self.cfg.file,
                    app_id=self.cfg.app_id,
                ),
                self.registry,
                self.scorer,
            )
            base = sub.expected_trial_count(ctx)
            return None if base is None else base * len(self.cfg.variants)
        except Exception:
            return None

    async def iter_trials(self, ctx: RunnerContext) -> AsyncIterator[Trial]:
        for variant in self.cfg.variants:
            # For now `overrides` are passed through trial metadata. The
            # actual application of overrides (e.g. swapping SOUL for one
            sub_cfg = WorkloadConfig(
                name=f"{self.cfg.name}/{variant.name}",
                file=self.cfg.file,
                app_id=self.cfg.app_id,
            )
            sub = WorkloadRunner(sub_cfg, self.registry, self.scorer)
            async for trial in sub.iter_trials(ctx):
                # Wrap execute() so the variant name is on every result.
                inner = trial.execute
                v_name = variant.name
                v_overrides = dict(variant.overrides)

                async def _exec(_inner=inner, _v=v_name, _o=v_overrides) -> TrialResult:
                    res = await _inner()
                    res.task_id = f"{_v}::{res.task_id}"
                    res.extra = {**res.extra, "variant": _v, "variant_overrides": _o}
                    return res

                yield Trial(
                    task_id=f"{variant.name}::{trial.task_id}",
                    instruction=trial.instruction,
                    execute=_exec,
                    metadata={
                        **trial.metadata,
                        "variant": variant.name,
                        "variant_overrides": variant.overrides,
                    },
                )
