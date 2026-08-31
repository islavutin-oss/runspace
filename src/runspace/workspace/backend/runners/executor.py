"""Drive a Runner — iterate trials, execute them, write to the session."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .base import Runner, RunnerContext, Trial, TrialResult

log = logging.getLogger(__name__)

DEFAULT_TRIAL_TIMEOUT_S = 300


async def execute_run(
    runner: Runner,
    ctx: RunnerContext,
    *,
    parallel: int = 1,
    trial_timeout_s: float = DEFAULT_TRIAL_TIMEOUT_S,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """Iterate `runner.iter_trials(ctx)`, execute each (parallel-bounded),
    write traces + results to `ctx.session`. Returns a summary dict the
    caller can stamp on session.close()."""

    parallel = max(1, parallel)
    sem = asyncio.Semaphore(parallel)
    started = time.time()
    n_trials = 0
    n_passed = 0
    errors: list[str] = []

    async def _run_one(trial: Trial) -> TrialResult | None:
        async with sem:
            try:
                result = await asyncio.wait_for(trial.execute(), timeout=trial_timeout_s)
            except asyncio.TimeoutError:
                log.warning(
                    "[runner] trial %s timed out after %.0fs", trial.task_id, trial_timeout_s
                )
                result = TrialResult(
                    task_id=trial.task_id,
                    score=0.0,
                    score_detail=[f"timeout after {trial_timeout_s:.0f}s"],
                    extra={"timeout": True, **trial.metadata},
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "[runner] trial %s execute crashed: %s", trial.task_id, exc, exc_info=True
                )
                errors.append(f"{trial.task_id}: {exc}")
                result = TrialResult(
                    task_id=trial.task_id,
                    score=0.0,
                    score_detail=[f"runner error: {exc}"],
                    extra={"error": str(exc), **trial.metadata},
                )

            # Write trial result to the session folder. The session writer
            # is the dashboard's source of truth — same path harness's
            # main_ecom.py uses.
            try:
                ctx.session.write_trial_result(
                    task_id=result.task_id,
                    trial_id=f"{ctx.session.run_id}-{result.task_id}",
                    score=result.score,
                    score_detail=result.score_detail,
                    elapsed_s=result.elapsed_s,
                    passed=result.score >= 1.0,
                    extra={
                        "llm_calls": result.llm_calls,
                        "tool_calls": result.tool_calls,
                        "tools_used": result.tools_used,
                        "final_text": result.final_text,
                        "instruction": trial.instruction,
                        "cost_cents": result.cost_cents,
                        "business_value": result.business_value,
                        **result.extra,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "[runner] failed to write trial result for %s: %s",
                    trial.task_id,
                    exc,
                    exc_info=True,
                )

            if on_progress is not None:
                try:
                    await on_progress(result)
                except Exception:
                    log.debug("on_progress callback failed", exc_info=True)
            return result

    pending: list[asyncio.Task[TrialResult | None]] = []
    async for trial in runner.iter_trials(ctx):
        n_trials += 1
        pending.append(asyncio.create_task(_run_one(trial)))

    results = await asyncio.gather(*pending, return_exceptions=False)
    for r in results:
        if r is not None and r.score >= 1.0:
            n_passed += 1

    elapsed = round(time.time() - started, 1)
    summary = {
        "runner": runner.name,
        "runner_type": runner.type,
        "n_trials": n_trials,
        "n_passed": n_passed,
        "score_pct": round(100.0 * n_passed / n_trials, 1) if n_trials else None,
        "elapsed_s": elapsed,
        "errors": errors[:20],
    }
    return summary
