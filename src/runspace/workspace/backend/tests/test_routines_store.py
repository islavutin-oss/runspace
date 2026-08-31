"""Unit tests for WorkspaceRoutinesStore."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

import pytest
import yaml

pytest.importorskip("agentino")

from agentino.scheduler import CronJob, Payload, Schedule, ScheduleKind  # noqa: E402

from runspace.workspace.backend.routines_store import WorkspaceRoutinesStore  # noqa: E402


def _make_store(tmp_path: Path, yaml_content: str | None = None):
    routines_file = tmp_path / "routines.yml"
    if yaml_content is not None:
        routines_file.write_text(yaml_content)
    return WorkspaceRoutinesStore(
        routines_file=routines_file,
        tenant_id="acme",
        registry=None,
    )


# ─── round-trip ────────────────────────────────────────────────────────


def test_list_reads_human_friendly_yaml(tmp_path):
    store = _make_store(
        tmp_path,
        """
routines:
  ada-morning-digest:
    agent_id: accountant
    schedule: "0 8 * * *"
    prompt: Morning AP digest
    description: Daily invoice summary
    enabled: true
    delivery:
      kind: channel
      target: general
""",
    )
    jobs = store.list()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "ada-morning-digest"
    assert job.name == "ada-morning-digest"
    assert job.tenant_id == "acme"
    assert job.schedule.cron_expr == "0 8 * * *"
    assert job.payload.kind == "routine"
    assert job.payload.data["agent_id"] == "accountant"
    assert job.payload.data["prompt"] == "Morning AP digest"
    assert job.payload.data["delivery"] == {"kind": "channel", "target": "general"}
    assert job.enabled is True


def test_list_tolerates_flat_yaml(tmp_path):
    """Hand-written tenant configs sometimes drop the `routines:` wrapper.
    The store should still parse them — fail gracefully, not silently.
    """
    store = _make_store(
        tmp_path,
        """
luca-anomaly-alert:
  agent_id: analytics
  schedule: "0 8 * * *"
  prompt: Run anomaly check
  enabled: true
""",
    )
    jobs = store.list()
    assert len(jobs) == 1
    assert jobs[0].id == "luca-anomaly-alert"


def test_skips_invalid_entries_without_failing_others(tmp_path):
    """A bad row shouldn't poison the whole list."""
    store = _make_store(
        tmp_path,
        """
routines:
  good-one:
    agent_id: accountant
    schedule: "0 8 * * *"
    prompt: do work
  broken-no-prompt:
    agent_id: accountant
    schedule: "0 8 * * *"
""",
    )
    jobs = store.list()
    ids = sorted(j.id for j in jobs)
    assert ids == ["good-one"]


def test_upsert_writes_yaml_and_state(tmp_path):
    store = _make_store(tmp_path)
    job = CronJob(
        id="test-routine",
        tenant_id="acme",
        name="test-routine",
        schedule=Schedule(kind=ScheduleKind.CRON, cron_expr="*/5 * * * *"),
        payload=Payload(
            kind="routine",
            data={
                "agent_id": "accountant",
                "prompt": "hello",
                "description": "test",
                "delivery": {"kind": "channel", "target": "general"},
            },
        ),
    )
    store.upsert(job)

    yaml_data = yaml.safe_load((tmp_path / "routines.yml").read_text())
    assert "routines" in yaml_data
    assert "test-routine" in yaml_data["routines"]
    entry = yaml_data["routines"]["test-routine"]
    assert entry["agent_id"] == "accountant"
    assert entry["schedule"] == "*/5 * * * *"
    assert entry["prompt"] == "hello"
    assert entry["delivery"] == {"kind": "channel", "target": "general"}

    state_data = json.loads((tmp_path / ".routines-state.json").read_text())
    assert "test-routine" in state_data


def test_upsert_then_list_round_trip(tmp_path):
    store = _make_store(tmp_path)
    job = CronJob(
        id="rt",
        tenant_id="acme",
        name="rt",
        schedule=Schedule(kind=ScheduleKind.CRON, cron_expr="0 9 * * *"),
        payload=Payload(
            kind="routine",
            data={
                "agent_id": "finance",
                "prompt": "p",
            },
        ),
    )
    store.upsert(job)
    jobs = store.list()
    assert len(jobs) == 1
    assert jobs[0].schedule.cron_expr == "0 9 * * *"
    assert jobs[0].payload.data["agent_id"] == "finance"


def test_silent_delivery_omitted_from_yaml(tmp_path):
    """Default `kind: silent` is the implicit default — don't pollute
    the yaml with it. Channel/dm deliveries DO get serialised.
    """
    store = _make_store(tmp_path)
    silent_job = CronJob(
        id="silent-one",
        tenant_id="acme",
        name="silent-one",
        schedule=Schedule(kind=ScheduleKind.CRON, cron_expr="0 9 * * *"),
        payload=Payload(
            kind="routine",
            data={
                "agent_id": "finance",
                "prompt": "p",
                "delivery": {"kind": "silent", "target": None},
            },
        ),
    )
    store.upsert(silent_job)
    yaml_data = yaml.safe_load((tmp_path / "routines.yml").read_text())
    assert "delivery" not in yaml_data["routines"]["silent-one"]


# ─── delete ────────────────────────────────────────────────────────────


def test_delete_removes_from_yaml_and_state(tmp_path):
    store = _make_store(tmp_path)
    job = CronJob(
        id="doomed",
        tenant_id="acme",
        name="doomed",
        schedule=Schedule(kind=ScheduleKind.CRON, cron_expr="0 9 * * *"),
        payload=Payload(
            kind="routine",
            data={
                "agent_id": "finance",
                "prompt": "p",
            },
        ),
    )
    store.upsert(job)
    assert len(store.list()) == 1

    store.delete("doomed")
    assert store.list() == []

    state = json.loads((tmp_path / ".routines-state.json").read_text())
    assert "doomed" not in state


def test_delete_unknown_id_is_noop(tmp_path):
    store = _make_store(tmp_path)
    store.delete("never-existed")  # should not raise
    assert store.list() == []


# ─── state isolation ──────────────────────────────────────────────────


def test_state_lives_in_separate_file(tmp_path):
    """Definitions yaml stays clean of runtime state — that's the
    whole point of the split. State only ends up in the json.
    """
    store = _make_store(tmp_path)
    job = CronJob(
        id="x",
        tenant_id="acme",
        name="x",
        schedule=Schedule(kind=ScheduleKind.CRON, cron_expr="0 9 * * *"),
        payload=Payload(
            kind="routine",
            data={
                "agent_id": "finance",
                "prompt": "p",
            },
        ),
        last_run_at=datetime(2026, 5, 3, 9, 0),
        consecutive_failures=2,
    )
    store.upsert(job)

    yaml_text = (tmp_path / "routines.yml").read_text()
    # None of the runtime keys leak into the yaml definition.
    for k in (
        "last_run_at",
        "next_run_at",
        "consecutive_failures",
        "last_error",
        "retry_after",
        "status",
    ):
        assert k not in yaml_text, f"runtime key {k} leaked into routines.yml"

    state_text = (tmp_path / ".routines-state.json").read_text()
    assert "consecutive_failures" in state_text
    assert "2" in state_text


def test_list_merges_state_back_into_cronjob(tmp_path):
    store = _make_store(tmp_path)
    job = CronJob(
        id="x",
        tenant_id="acme",
        name="x",
        schedule=Schedule(kind=ScheduleKind.CRON, cron_expr="0 9 * * *"),
        payload=Payload(
            kind="routine",
            data={
                "agent_id": "finance",
                "prompt": "p",
            },
        ),
        consecutive_failures=3,
        last_error="boom",
    )
    store.upsert(job)

    rebuilt = store.list()[0]
    assert rebuilt.consecutive_failures == 3
    assert rebuilt.last_error == "boom"


# ─── concurrency ──────────────────────────────────────────────────────


def test_concurrent_upserts_do_not_lose_writes(tmp_path):
    """Two threads upserting different routines simultaneously — flock
    must serialise so neither write is dropped. Without the lock, the
    second writer's read-modify-write would clobber the first.
    """
    store = _make_store(tmp_path)
    barrier = threading.Barrier(2)

    def make_job(rid):
        return CronJob(
            id=rid,
            tenant_id="acme",
            name=rid,
            schedule=Schedule(kind=ScheduleKind.CRON, cron_expr="0 9 * * *"),
            payload=Payload(
                kind="routine",
                data={
                    "agent_id": "finance",
                    "prompt": rid,
                },
            ),
        )

    def worker(rid):
        barrier.wait()
        store.upsert(make_job(rid))

    threads = [threading.Thread(target=worker, args=(f"routine-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = sorted(j.id for j in store.list())
    assert ids == [f"routine-{i}" for i in range(8)], f"lost writes under contention: got {ids}"


# ─── record_run audit trail ──────────────────────────────────────────


def test_record_run_appends_to_runs_file(tmp_path):
    store = _make_store(tmp_path)
    store.record_run("rid", True, datetime(2026, 5, 3, 9, 0))
    store.record_run("rid", False, datetime(2026, 5, 3, 9, 5), error="oops")

    runs_file = tmp_path / ".routines-runs.json"
    assert runs_file.exists()
    blob = json.loads(runs_file.read_text())
    assert "rid" in blob
    assert len(blob["rid"]) == 2
    assert blob["rid"][1]["error"] == "oops"


def test_record_run_caps_at_500_entries(tmp_path):
    store = _make_store(tmp_path)
    for i in range(550):
        store.record_run("rid", True, datetime(2026, 5, 3, 9, 0))
    blob = json.loads((tmp_path / ".routines-runs.json").read_text())
    assert len(blob["rid"]) == 500


# ─── empty / edge ─────────────────────────────────────────────────────


def test_empty_routines_file_lists_nothing(tmp_path):
    store = _make_store(tmp_path)
    assert store.list() == []


def test_init_creates_yaml_if_missing(tmp_path):
    # Sub-directory must also be created.
    routines = tmp_path / "subdir" / "routines.yml"
    WorkspaceRoutinesStore(routines, tenant_id="acme")
    assert routines.exists()
    assert "routines:" in routines.read_text()
