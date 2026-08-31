"""Scheduling primitives — runtime-agnostic data shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from croniter import croniter


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DISABLED = "disabled"


class ScheduleKind(Enum):
    AT = "at"
    CRON = "cron"
    EVERY = "every"


@dataclass
class Schedule:
    kind: ScheduleKind
    at: datetime | None = None
    cron_expr: str | None = None
    every_ms: int | None = None
    timezone: str = "Europe/Nicosia"

    def next_run(self, after: datetime | None = None) -> datetime | None:
        after = after or datetime.now()
        if self.kind == ScheduleKind.AT:
            if self.at and self.at > after:
                return self.at
            return None
        if self.kind == ScheduleKind.CRON:
            if self.cron_expr:
                try:
                    cron = croniter(self.cron_expr, after)
                    return cron.get_next(datetime)
                except Exception:
                    return None
        if self.kind == ScheduleKind.EVERY:
            if self.every_ms:
                return after + timedelta(milliseconds=self.every_ms)
        return None


@dataclass
class Delivery:
    channel: str = "whatsapp"
    to: str | None = None
    best_effort: bool = True


@dataclass
class Payload:
    kind: str
    skill: str | None = None
    template: str | None = None
    message: str | None = None
    data: dict = field(default_factory=dict)


@dataclass
class CronJob:
    id: str
    tenant_id: str
    name: str
    schedule: Schedule
    payload: Payload
    delivery: Delivery | None = None

    enabled: bool = True
    status: JobStatus = JobStatus.PENDING
    delete_after_run: bool = False

    created_at: datetime = field(default_factory=datetime.now)
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_error: str | None = None

    consecutive_failures: int = 0
    retry_after: datetime | None = None

    def __post_init__(self):
        if self.next_run_at is None:
            self.next_run_at = self.schedule.next_run()

    def calculate_retry_delay(self) -> timedelta:
        delays = [30, 60, 300, 900, 3600]
        idx = min(self.consecutive_failures, len(delays) - 1)
        return timedelta(seconds=delays[idx])

    def mark_success(self):
        self.status = JobStatus.COMPLETED
        self.last_run_at = datetime.now()
        self.last_error = None
        self.consecutive_failures = 0
        self.retry_after = None
        if self.schedule.kind != ScheduleKind.AT:
            self.next_run_at = self.schedule.next_run(self.last_run_at)
            self.status = JobStatus.PENDING
        else:
            if self.delete_after_run:
                pass
            else:
                self.enabled = False

    def mark_failure(self, error: str):
        self.status = JobStatus.FAILED
        self.last_run_at = datetime.now()
        self.last_error = error
        self.consecutive_failures += 1
        delay = self.calculate_retry_delay()
        self.retry_after = datetime.now() + delay
        if self.schedule.kind != ScheduleKind.AT:
            self.next_run_at = self.retry_after
            self.status = JobStatus.PENDING

    def is_due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if not self.enabled:
            return False
        if self.status == JobStatus.RUNNING:
            return False

        def to_naive(dt):
            if dt is None:
                return None
            return dt.replace(tzinfo=None) if dt.tzinfo else dt

        now_naive = to_naive(now)
        if self.retry_after and now_naive < to_naive(self.retry_after):
            return False
        if self.next_run_at and now_naive >= to_naive(self.next_run_at):
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "schedule_kind": self.schedule.kind.value,
            "schedule_at": self.schedule.at.isoformat() if self.schedule.at else None,
            "schedule_cron": self.schedule.cron_expr,
            "schedule_every_ms": self.schedule.every_ms,
            "schedule_timezone": self.schedule.timezone,
            "payload_kind": self.payload.kind,
            "payload_skill": self.payload.skill,
            "payload_template": self.payload.template,
            "payload_message": self.payload.message,
            "payload_data": self.payload.data,
            "delivery_channel": self.delivery.channel if self.delivery else None,
            "delivery_to": self.delivery.to if self.delivery else None,
            "delivery_best_effort": self.delivery.best_effort if self.delivery else True,
            "enabled": self.enabled,
            "status": self.status.value,
            "delete_after_run": self.delete_after_run,
            "created_at": self.created_at.isoformat(),
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "retry_after": self.retry_after.isoformat() if self.retry_after else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CronJob:
        def _parse_dt(val):
            if not val:
                return None
            dt = datetime.fromisoformat(val) if isinstance(val, str) else val
            return dt.replace(tzinfo=None) if dt.tzinfo else dt

        schedule = Schedule(
            kind=ScheduleKind(data["schedule_kind"]),
            at=_parse_dt(data.get("schedule_at")),
            cron_expr=data.get("schedule_cron"),
            every_ms=data.get("schedule_every_ms"),
            timezone=data.get("schedule_timezone", "Europe/Nicosia"),
        )
        payload = Payload(
            kind=data["payload_kind"],
            skill=data.get("payload_skill"),
            template=data.get("payload_template"),
            message=data.get("payload_message"),
            data=data.get("payload_data", {}),
        )
        delivery = None
        if data.get("delivery_channel"):
            delivery = Delivery(
                channel=data["delivery_channel"],
                to=data.get("delivery_to"),
                best_effort=data.get("delivery_best_effort", True),
            )
        return cls(
            id=data["id"],
            tenant_id=data["tenant_id"],
            name=data["name"],
            schedule=schedule,
            payload=payload,
            delivery=delivery,
            enabled=data.get("enabled", True),
            status=JobStatus(data.get("status", "pending")),
            delete_after_run=data.get("delete_after_run", False),
            created_at=_parse_dt(data.get("created_at")) or datetime.now(),
            next_run_at=_parse_dt(data.get("next_run_at")),
            last_run_at=_parse_dt(data.get("last_run_at")),
            last_error=data.get("last_error"),
            consecutive_failures=data.get("consecutive_failures", 0),
            retry_after=_parse_dt(data.get("retry_after")),
        )


__all__ = [
    "JobStatus",
    "ScheduleKind",
    "Schedule",
    "Delivery",
    "Payload",
    "CronJob",
]
