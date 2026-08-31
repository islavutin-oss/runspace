"""Tests for the runner abstraction (workspace/backend/runners)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from runspace.workspace.backend.runners.ab import ABConfig, ABRunner, Variant
from runspace.workspace.backend.runners.base import RunnerContext
from runspace.workspace.backend.runners.executor import execute_run
from runspace.workspace.backend.runners.loader import load_runners
from runspace.workspace.backend.runners.workload import WorkloadConfig, WorkloadRunner
from runspace.workspace.backend.scoring.base import ScorerInput
from runspace.workspace.backend.scoring.match import TokenMatchScorer


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fixtures: minimal AppRegistry stub + SessionWriter stub
# ---------------------------------------------------------------------------


class _StubRegistry:
    """Records chat() calls. Returns a canned reply per task_id."""

    def __init__(self, replies: dict[str, str] | None = None):
        self.calls: list[tuple[str, str, str]] = []
        self.replies = replies or {}

    async def chat(self, app_id: str, message: str, session_id: str) -> dict:
        self.calls.append((app_id, message, session_id))
        text = self.replies.get(message, "default reply")
        return {"text": text, "tools_used": [], "llm_calls": 1}


class _StubSession:
    """Captures everything write_trial_result/write_trace gets called with."""

    def __init__(self, run_id: str = "test-run"):
        self.run_id = run_id
        self.session_dir = Path("/tmp/_stub_session")
        self.results: list[dict[str, Any]] = []
        self.traces: list[tuple[str, str, str]] = []
        self._closed = False

    def write_trial_result(
        self, *, task_id, trial_id, score, score_detail, elapsed_s, passed, extra=None
    ):
        self.results.append(
            {
                "task_id": task_id,
                "trial_id": trial_id,
                "score": score,
                "score_detail": score_detail,
                "elapsed_s": elapsed_s,
                "passed": passed,
                "extra": extra or {},
            }
        )

    def write_trace(self, task_id, trial_id, stdout, stderr="", extra=None):
        self.traces.append((task_id, trial_id, stdout))

    def close(self, *, status="ok", extra=None):
        self._closed = True


# ---------------------------------------------------------------------------
# WorkloadRunner — reads JSONL, executes each row
# ---------------------------------------------------------------------------


def test_workload_loads_and_iterates(tmp_path: Path):
    wl = tmp_path / "wl.jsonl"
    wl.write_text(
        json.dumps({"task_id": "a", "instruction": "hi", "expected": "hello"})
        + "\n"
        + json.dumps({"task_id": "b", "instruction": "bye", "expected": "see ya"})
        + "\n"
    )
    reg = _StubRegistry()
    runner = WorkloadRunner(WorkloadConfig(name="wl", file="wl.jsonl", app_id="x"), reg)
    ctx = RunnerContext(project="p", project_root=tmp_path, session=_StubSession(), settings={})

    async def collect():
        out = []
        async for t in runner.iter_trials(ctx):
            out.append(t.task_id)
        return out

    assert _run(collect()) == ["a", "b"]
    assert runner.expected_trial_count(ctx) == 2


def test_workload_missing_instruction_rejected(tmp_path: Path):
    wl = tmp_path / "wl.jsonl"
    wl.write_text(json.dumps({"task_id": "a"}) + "\n")  # no instruction
    runner = WorkloadRunner(WorkloadConfig(name="wl", file="wl.jsonl", app_id="x"), _StubRegistry())
    ctx = RunnerContext(project="p", project_root=tmp_path, session=_StubSession(), settings={})
    with pytest.raises(ValueError, match="missing 'instruction'"):

        async def go():
            async for _ in runner.iter_trials(ctx):
                pass

        _run(go())


def test_workload_path_traversal_blocked(tmp_path: Path):
    runner = WorkloadRunner(
        WorkloadConfig(name="wl", file="../../etc/passwd", app_id="x"), _StubRegistry()
    )
    ctx = RunnerContext(project="p", project_root=tmp_path, session=_StubSession(), settings={})
    with pytest.raises(ValueError, match="escapes project root"):

        async def go():
            async for _ in runner.iter_trials(ctx):
                pass

        _run(go())


def test_workload_executes_through_registry(tmp_path: Path):
    """Trial.execute() routes through registry.chat()."""
    wl = tmp_path / "wl.jsonl"
    wl.write_text(json.dumps({"task_id": "ping", "instruction": "ping"}) + "\n")
    reg = _StubRegistry(replies={"ping": "pong"})
    runner = WorkloadRunner(WorkloadConfig(name="wl", file="wl.jsonl", app_id="dash"), reg)
    session = _StubSession()
    ctx = RunnerContext(project="p", project_root=tmp_path, session=session, settings={})

    async def go():
        async for trial in runner.iter_trials(ctx):
            return await trial.execute()

    result = _run(go())
    assert result.task_id == "ping"
    assert result.final_text == "pong"
    assert reg.calls and reg.calls[0][0] == "dash"


def test_workload_with_token_scorer_passes_when_token_present(tmp_path: Path):
    wl = tmp_path / "wl.jsonl"
    wl.write_text(json.dumps({"task_id": "yn", "instruction": "?", "expected": "<YES>"}) + "\n")
    reg = _StubRegistry(replies={"?": "Reply: <YES> we have it."})
    runner = WorkloadRunner(
        WorkloadConfig(name="wl", file="wl.jsonl", app_id="x"),
        reg,
        scorer=TokenMatchScorer(),
    )
    ctx = RunnerContext(project="p", project_root=tmp_path, session=_StubSession(), settings={})

    async def go():
        async for trial in runner.iter_trials(ctx):
            return await trial.execute()

    res = _run(go())
    assert res.score == 1.0
    assert res.score_detail == []


def test_workload_with_token_scorer_fails_with_explanation(tmp_path: Path):
    wl = tmp_path / "wl.jsonl"
    wl.write_text(
        json.dumps({"task_id": "yn", "instruction": "?", "expected": ["<YES>", "<COUNT:3>"]}) + "\n"
    )
    reg = _StubRegistry(replies={"?": "Reply: <YES> we have it."})
    runner = WorkloadRunner(
        WorkloadConfig(name="wl", file="wl.jsonl", app_id="x"),
        reg,
        scorer=TokenMatchScorer(),
    )
    ctx = RunnerContext(project="p", project_root=tmp_path, session=_StubSession(), settings={})

    async def go():
        async for trial in runner.iter_trials(ctx):
            return await trial.execute()

    res = _run(go())
    assert res.score == 0.0
    assert "<COUNT:3>" in " ".join(res.score_detail)


# ---------------------------------------------------------------------------
# Executor — drives the runner, writes session, handles parallelism + errors
# ---------------------------------------------------------------------------


def test_executor_writes_results_to_session(tmp_path: Path):
    wl = tmp_path / "wl.jsonl"
    wl.write_text(
        json.dumps({"task_id": "a", "instruction": "x", "expected": "x"})
        + "\n"
        + json.dumps({"task_id": "b", "instruction": "y", "expected": "y"})
        + "\n"
    )
    reg = _StubRegistry(replies={"x": "x", "y": "y"})
    session = _StubSession()
    runner = WorkloadRunner(
        WorkloadConfig(name="wl", file="wl.jsonl", app_id="x"),
        reg,
        scorer=TokenMatchScorer(),
    )
    ctx = RunnerContext(project="p", project_root=tmp_path, session=session, settings={})
    summary = _run(execute_run(runner, ctx, parallel=2))
    assert summary["n_trials"] == 2
    assert summary["n_passed"] == 2
    assert summary["score_pct"] == 100.0
    task_ids = {r["task_id"] for r in session.results}
    assert task_ids == {"a", "b"}


def test_executor_records_failures(tmp_path: Path):
    wl = tmp_path / "wl.jsonl"
    wl.write_text(json.dumps({"task_id": "boom", "instruction": "boom"}) + "\n")

    class _BadReg(_StubRegistry):
        async def chat(self, *a, **kw):
            raise RuntimeError("forced")

    session = _StubSession()
    runner = WorkloadRunner(
        WorkloadConfig(name="wl", file="wl.jsonl", app_id="x"),
        _BadReg(),
    )
    ctx = RunnerContext(project="p", project_root=tmp_path, session=session, settings={})
    summary = _run(execute_run(runner, ctx, parallel=1, trial_timeout_s=2))
    assert summary["n_passed"] == 0
    assert summary["n_trials"] == 1
    # Trial result captured the error
    assert "agent error" in " ".join(session.results[0]["score_detail"])


def test_executor_respects_timeout(tmp_path: Path):
    """Trials that block past the timeout produce a 0-score result with
    a 'timeout' detail — they don't crash the run."""
    wl = tmp_path / "wl.jsonl"
    wl.write_text(json.dumps({"task_id": "slow", "instruction": "slow"}) + "\n")

    class _SlowReg:
        async def chat(self, *a, **kw):
            await asyncio.sleep(5)
            return {"text": "done"}

    session = _StubSession()
    runner = WorkloadRunner(
        WorkloadConfig(name="wl", file="wl.jsonl", app_id="x"),
        _SlowReg(),
    )
    ctx = RunnerContext(project="p", project_root=tmp_path, session=session, settings={})
    summary = _run(execute_run(runner, ctx, parallel=1, trial_timeout_s=0.2))
    assert summary["n_trials"] == 1
    assert summary["n_passed"] == 0
    assert "timeout" in " ".join(session.results[0]["score_detail"]).lower()


# ---------------------------------------------------------------------------
# AB runner — workload × variants
# ---------------------------------------------------------------------------


def test_ab_runner_yields_workload_x_variants(tmp_path: Path):
    wl = tmp_path / "wl.jsonl"
    wl.write_text(
        json.dumps({"task_id": "q1", "instruction": "x"})
        + "\n"
        + json.dumps({"task_id": "q2", "instruction": "y"})
        + "\n"
    )
    runner = ABRunner(
        ABConfig(
            name="ab",
            file="wl.jsonl",
            app_id="x",
            variants=[Variant(name="A"), Variant(name="B", overrides={"temperature": 0.7})],
        ),
        _StubRegistry(),
    )
    ctx = RunnerContext(project="p", project_root=tmp_path, session=_StubSession(), settings={})

    async def collect():
        ids = []
        async for t in runner.iter_trials(ctx):
            ids.append(t.task_id)
        return ids

    ids = _run(collect())
    # 2 workload rows × 2 variants = 4
    assert len(ids) == 4
    assert "A::q1" in ids and "B::q2" in ids
    assert runner.expected_trial_count(ctx) == 4


def test_ab_executor_tags_results_with_variant(tmp_path: Path):
    wl = tmp_path / "wl.jsonl"
    wl.write_text(json.dumps({"task_id": "q", "instruction": "x"}) + "\n")
    session = _StubSession()
    runner = ABRunner(
        ABConfig(
            name="ab",
            file="wl.jsonl",
            app_id="x",
            variants=[Variant(name="prod"), Variant(name="tuned")],
        ),
        _StubRegistry(replies={"x": "ok"}),
    )
    ctx = RunnerContext(project="p", project_root=tmp_path, session=session, settings={})
    _run(execute_run(runner, ctx, parallel=2))
    variants = {r["extra"].get("variant") for r in session.results}
    assert variants == {"prod", "tuned"}


# ---------------------------------------------------------------------------
# Loader — runners.yml + scorer resolution
# ---------------------------------------------------------------------------


def test_loader_parses_workload_runner(tmp_path: Path):
    (tmp_path / "wl.jsonl").write_text(json.dumps({"task_id": "a", "instruction": "x"}) + "\n")
    yml = tmp_path / "runners.yml"
    yml.write_text("""
runners:
  smoke:
    type: workload
    description: "Smoke test"
    file: wl.jsonl
    app_id: x
    scorer: token_match
""")
    runners = load_runners(yml, _StubRegistry())
    assert "smoke" in runners
    assert runners["smoke"].type == "workload"
    assert runners["smoke"].description == "Smoke test"


def test_loader_rejects_scorer_path_traversal(tmp_path: Path):
    yml = tmp_path / "runners.yml"
    yml.write_text("""
runners:
  bad:
    type: workload
    file: wl.jsonl
    app_id: x
    scorer: ../../../etc/passwd
""")
    with pytest.raises(ValueError, match="escapes project root"):
        load_runners(yml, _StubRegistry())


def test_loader_loads_project_python_scorer(tmp_path: Path):
    """A project can drop a .py scorer and reference it by path."""
    (tmp_path / "wl.jsonl").write_text(json.dumps({"task_id": "a", "instruction": "x"}) + "\n")
    scorer_py = tmp_path / "my_scorer.py"
    scorer_py.write_text("""
from runspace.workspace.backend.scoring.base import Scorer, ScorerInput, ScorerVerdict
class Passer(Scorer):
    name = "passer"
    async def judge(self, inp):
        return ScorerVerdict(score=1.0, score_detail=["always"])
""")
    yml = tmp_path / "runners.yml"
    yml.write_text("""
runners:
  always_pass:
    type: workload
    file: wl.jsonl
    app_id: x
    scorer: my_scorer.py
""")
    runners = load_runners(yml, _StubRegistry())
    assert runners["always_pass"].scorer.name == "passer"


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------


def test_token_match_scorer_handles_list_and_string(tmp_path: Path):
    s = TokenMatchScorer()
    v = _run(s.judge(ScorerInput(instruction="?", final_text="<YES> hi", expected="<YES>")))
    assert v.score == 1.0
    v = _run(
        s.judge(ScorerInput(instruction="?", final_text="<YES> hi", expected=["<YES>", "<NO>"]))
    )
    assert v.score == 0.0
    assert "<NO>" in v.score_detail[0]
