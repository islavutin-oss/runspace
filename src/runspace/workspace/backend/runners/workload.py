"""Workload runner — runs a JSONL of tasks through one of the project's apps."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .base import Runner, RunnerContext, Trial, TrialResult

log = logging.getLogger(__name__)


@dataclass
class WorkloadConfig:
    """Parsed `runners.yml` entry for `type: workload`."""

    name: str
    file: str  # path relative to project root
    app_id: str  # which app in workspace.yml to invoke
    scorer: str | None = None  # path or builtin name
    description: str = ""


class WorkloadRunner(Runner):
    type = "workload"

    def __init__(self, cfg: WorkloadConfig, registry: Any, scorer: Any | None = None):
        self.name = cfg.name
        self.description = cfg.description
        self.cfg = cfg
        self.registry = registry  # AppRegistry — has .chat() and apps
        self.scorer = scorer

    def _load_rows(self, ctx: RunnerContext) -> list[dict[str, Any]]:
        path = (ctx.project_root / self.cfg.file).resolve()
        try:
            path.relative_to(ctx.project_root.resolve())
        except ValueError:
            raise ValueError(f"workload file escapes project root: {path}")
        if not path.is_file():
            raise FileNotFoundError(f"workload not found: {path}")
        rows: list[dict[str, Any]] = []
        with open(path) as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"workload {path}:{i + 1}: {exc}")
                if "instruction" not in obj:
                    raise ValueError(f"workload {path}:{i + 1}: missing 'instruction'")
                obj.setdefault("task_id", f"row-{i:04d}")
                rows.append(obj)
        return rows

    def expected_trial_count(self, ctx: RunnerContext) -> int | None:
        try:
            return len(self._load_rows(ctx))
        except (FileNotFoundError, ValueError):
            return None

    async def iter_trials(self, ctx: RunnerContext) -> AsyncIterator[Trial]:
        rows = self._load_rows(ctx)
        for row in rows:
            yield self._make_trial(row, ctx)

    def _make_trial(self, row: dict[str, Any], ctx: RunnerContext) -> Trial:
        task_id = row["task_id"]
        instruction = row["instruction"]
        expected = row.get("expected")
        metadata = row.get("metadata") or {}

        async def execute() -> TrialResult:
            t0 = time.time()
            session_id = f"runner-{ctx.session.run_id}-{task_id}"
            try:
                result = await self.registry.chat(
                    self.cfg.app_id,
                    instruction,
                    session_id,
                )
            except Exception as exc:  # noqa: BLE001
                elapsed = time.time() - t0
                log.error("[workload] %s execute failed: %s", task_id, exc, exc_info=True)
                return TrialResult(
                    task_id=task_id,
                    score=0.0,
                    score_detail=[f"agent error: {exc}"],
                    elapsed_s=elapsed,
                    llm_calls=0,
                    tool_calls=0,
                    final_text="",
                    tools_used=[],
                    extra={"error": str(exc), **metadata},
                )

            final_text = result.get("text") or result.get("response") or ""
            tools_used = result.get("tools_used") or []

            # Score (if a scorer was configured for this runner)
            score = 0.0
            score_detail: list[str] = []
            score_extra: dict[str, Any] = {}
            if self.scorer is not None:
                from ..scoring.base import ScorerInput

                verdict = await self.scorer.judge(
                    ScorerInput(
                        instruction=instruction,
                        final_text=final_text,
                        tools_used=tools_used,
                        expected=expected,
                        metadata=metadata,
                    )
                )
                score = verdict.score
                score_detail = list(verdict.score_detail)
                score_extra = dict(verdict.extra)

            elapsed = time.time() - t0
            return TrialResult(
                task_id=task_id,
                score=score,
                score_detail=score_detail,
                elapsed_s=elapsed,
                llm_calls=result.get("llm_calls", 0),
                tool_calls=len(tools_used),
                tools_used=tools_used,
                final_text=final_text,
                extra={"app_id": self.cfg.app_id, **metadata, **score_extra},
            )

        return Trial(
            task_id=task_id,
            instruction=instruction,
            execute=execute,
            metadata={"app_id": self.cfg.app_id, "expected": expected, **metadata},
        )
