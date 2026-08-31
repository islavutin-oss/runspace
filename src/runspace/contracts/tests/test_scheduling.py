"""Scheduling contract — pin the data shapes + structural equivalence with agentino's parallel definitions."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum

import pytest


def test_contracts_scheduling_exports_data_types():
    from runspace.contracts.scheduling import (
        CronJob,
        Delivery,
        JobStatus,
        Payload,
        Schedule,
        ScheduleKind,
    )

    # Smoke: build a CronJob with a CRON schedule and a message payload.
    job = CronJob(
        id="r1",
        tenant_id="t",
        name="daily",
        schedule=Schedule(kind=ScheduleKind.CRON, cron_expr="0 7 * * *"),
        payload=Payload(kind="message", message="hi"),
        delivery=Delivery(channel="chat"),
    )
    assert job.status is JobStatus.PENDING
    assert job.next_run_at is not None  # __post_init__ computed it


def test_agentino_shape_matches_contracts():
    """Same field names + same enum members on both sides.

    These are parallel class objects (not the same class — see module
    docstring) but their SHAPE must match. If agentino adds a field
    or renames an enum member without runspace-contracts following,
    this test catches it at PR time.
    """
    try:
        from agentino import scheduler as ag
    except ImportError:
        pytest.skip("agentino not importable in this environment")
    from runspace.contracts import scheduling as co

    # Dataclasses must have the same field names (order doesn't matter).
    for name in ("Schedule", "Payload", "Delivery", "CronJob"):
        ag_cls = getattr(ag, name)
        co_cls = getattr(co, name)
        assert is_dataclass(ag_cls) and is_dataclass(co_cls), name
        ag_fields = {f.name for f in fields(ag_cls)}
        co_fields = {f.name for f in fields(co_cls)}
        assert ag_fields == co_fields, (
            f"{name} field-set drifted:\n"
            f"  agentino - contracts: {ag_fields - co_fields}\n"
            f"  contracts - agentino: {co_fields - ag_fields}\n"
            f"Update agentino/src/agentino/scheduler/core.py or "
            f"runspace/contracts/scheduling.py so they match again."
        )

    # Enums must have the same member names + .value strings.
    for name in ("ScheduleKind", "JobStatus"):
        ag_enum = getattr(ag, name)
        co_enum = getattr(co, name)
        assert issubclass(ag_enum, Enum) and issubclass(co_enum, Enum), name
        ag_members = {m.name: m.value for m in ag_enum}
        co_members = {m.name: m.value for m in co_enum}
        assert ag_members == co_members, (
            f"{name} enum drifted:\n  agentino: {ag_members}\n  contracts: {co_members}"
        )


def test_at_schedule_one_shot_after_passes():
    from runspace.contracts.scheduling import Schedule, ScheduleKind

    past = datetime(2020, 1, 1)
    s = Schedule(kind=ScheduleKind.AT, at=past)
    assert s.next_run() is None  # past one-shot has no next run
