"""WorkspaceRoutinesStore — file-as-truth JobStore for tenant routines."""

from __future__ import annotations

import fcntl
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import yaml

from runspace.contracts.scheduling import (
    CronJob,
    JobStatus,
    Payload,
    Schedule,
    ScheduleKind,
)

log = logging.getLogger(__name__)


_RUNTIME_KEYS = {
    "status",
    "next_run_at",
    "last_run_at",
    "last_error",
    "consecutive_failures",
    "retry_after",
}


class WorkspaceRoutinesStore:
    """JobStore backed by `routines.yml` + `.routines-state.json`.

    Constructed once per tenant by `WorkspaceGateway.from_config` when
    the workspace declares `routines: <path>`. The scheduler treats
    instances of this class as opaque `JobStore` implementations.
    """

    def __init__(
        self,
        routines_file: str | Path,
        tenant_id: str,
        registry=None,
    ) -> None:
        self._yaml_path = Path(routines_file)
        self._state_path = self._yaml_path.with_name(".routines-state.json")
        self._runs_path = self._yaml_path.with_name(".routines-runs.json")
        self._tenant_id = tenant_id
        # Registry is used to resolve sender_name/avatar/color when the
        # yaml omits them — we copy whatever the agent's app entry has.
        self._registry = registry
        # Touch the yaml file if it doesn't exist so editors / `cat` /
        # the UI never have to special-case "no routines yet".
        if not self._yaml_path.exists():
            self._yaml_path.parent.mkdir(parents=True, exist_ok=True)
            self._yaml_path.write_text("routines: {}\n")

    # ─── public protocol methods ──────────────────────────────────────

    def list(self, tenant_id: str | None = None) -> list[CronJob]:
        # tenant_id arg kept for protocol parity; this store is bound
        # to a single tenant, so we ignore it. (A multi-tenant
        # filesystem-store would need to walk all tenants.)
        defs = self._read_yaml()
        states = self._read_state()
        jobs: list[CronJob] = []
        for routine_id, cfg in defs.items():
            try:
                jobs.append(self._yaml_to_cronjob(routine_id, cfg, states.get(routine_id, {})))
            except Exception as e:
                log.warning("[routines_store] skipping %r: %s", routine_id, e)
        return jobs

    def upsert(self, job: CronJob) -> None:
        defn, state = self._cronjob_to_yaml(job)
        with self._yaml_lock():
            data = self._read_yaml_unlocked()
            data[job.name] = defn
            self._write_yaml_unlocked(data)
        with self._state_lock():
            states = self._read_state_unlocked()
            states[job.name] = state
            self._write_state_unlocked(states)

    def delete(self, job_id: str) -> None:
        with self._yaml_lock():
            data = self._read_yaml_unlocked()
            data.pop(job_id, None)
            self._write_yaml_unlocked(data)
        with self._state_lock():
            states = self._read_state_unlocked()
            states.pop(job_id, None)
            self._write_state_unlocked(states)

    def record_run(
        self,
        job_id: str,
        ok: bool,
        ts: datetime,
        error: str | None = None,
    ) -> None:
        # 500-entry ring buffer per job, same as agentino's FileJobStore.
        # Flock the runs file separately so audit writes don't block
        # state updates from the scheduler.
        with self._lock(self._runs_path):
            blob = self._read_runs_unlocked()
            runs = blob.get(job_id, [])
            runs.append({"ts": ts.isoformat(), "ok": bool(ok), "error": error})
            if len(runs) > 500:
                runs = runs[-500:]
            blob[job_id] = runs
            self._write_runs_unlocked(blob)

    # ─── schema mapping ───────────────────────────────────────────────

    def _yaml_to_cronjob(
        self,
        routine_id: str,
        cfg: dict,
        state: dict,
    ) -> CronJob:
        """Build a CronJob from one yaml entry + its state row.

        yaml entry shape (id-keyed, human-friendly):
            agent_id: <str>     required
            schedule: <cron>    required, croniter expression
            prompt: <str>       required
            description: <str>  optional, cosmetic
            enabled: <bool>     default True
            delivery:           optional, default {kind: silent}
              kind: channel|dm|silent
              target: <name>    required if kind in (channel, dm)
        """
        agent_id = cfg.get("agent_id")
        prompt = cfg.get("prompt")
        cron_expr = cfg.get("schedule") or cfg.get("cron")
        if not (agent_id and prompt and cron_expr):
            raise ValueError(f"routine {routine_id!r} missing one of agent_id/prompt/schedule")

        delivery_cfg = cfg.get("delivery") or {"kind": "silent", "target": None}

        # Persona resolved from registry so the chat post matches the
        # agent's UI avatar without forcing the yaml to repeat it.
        persona = self._persona_for(agent_id)

        payload = Payload(
            kind="routine",
            data={
                "agent_id": agent_id,
                "prompt": prompt,
                "description": cfg.get("description", routine_id),
                "delivery": {
                    "kind": delivery_cfg.get("kind", "silent"),
                    "target": delivery_cfg.get("target"),
                },
                "sender_name": cfg.get("sender_name") or persona.get("sender_name"),
                "sender_avatar": cfg.get("sender_avatar") or persona.get("sender_avatar"),
                "sender_color": cfg.get("sender_color") or persona.get("sender_color"),
            },
        )

        # Top-level Delivery left None — the executor reads from
        # payload.data.delivery (acme convention; see services/cron/
        # executors/routine.py). Top-level Delivery is the legacy
        # WhatsApp shape, not used for routines.

        schedule = Schedule(kind=ScheduleKind.CRON, cron_expr=cron_expr)

        def _parse_dt(val):
            if not val:
                return None
            return datetime.fromisoformat(val) if isinstance(val, str) else val

        job = CronJob(
            id=routine_id,
            tenant_id=self._tenant_id,
            name=routine_id,
            schedule=schedule,
            payload=payload,
            delivery=None,
            enabled=bool(cfg.get("enabled", True)),
            status=JobStatus(state.get("status", "pending")),
            next_run_at=_parse_dt(state.get("next_run_at")),
            last_run_at=_parse_dt(state.get("last_run_at")),
            last_error=state.get("last_error"),
            consecutive_failures=int(state.get("consecutive_failures", 0)),
            retry_after=_parse_dt(state.get("retry_after")),
        )
        return job

    def _cronjob_to_yaml(self, job: CronJob) -> tuple[dict, dict]:
        """Inverse of _yaml_to_cronjob — split a CronJob into the
        human-friendly definition dict (for routines.yml) and the
        runtime-state dict (for .routines-state.json)."""
        data = job.payload.data or {}
        defn = {
            "agent_id": data.get("agent_id", ""),
            "schedule": job.schedule.cron_expr or "",
            "prompt": data.get("prompt", ""),
            "description": data.get("description", job.name),
            "enabled": bool(job.enabled),
        }
        delivery = data.get("delivery")
        if delivery and delivery.get("kind") and delivery.get("kind") != "silent":
            defn["delivery"] = {
                "kind": delivery.get("kind"),
                "target": delivery.get("target"),
            }

        def _iso(dt):
            return dt.isoformat() if dt else None

        state = {
            "status": job.status.value if hasattr(job.status, "value") else job.status,
            "next_run_at": _iso(job.next_run_at),
            "last_run_at": _iso(job.last_run_at),
            "last_error": job.last_error,
            "consecutive_failures": job.consecutive_failures,
            "retry_after": _iso(job.retry_after),
        }
        return defn, state

    def _persona_for(self, agent_id: str) -> dict:
        if not self._registry:
            return {}
        apps = getattr(self._registry, "apps", None) or {}
        app = apps.get(agent_id)
        if not app:
            return {}
        return {
            "sender_name": getattr(app, "name", agent_id.capitalize()),
            "sender_avatar": getattr(app, "avatar", "🤖"),
            "sender_color": getattr(app, "color", "#6B7280"),
        }

    # ─── flock-protected file IO ──────────────────────────────────────

    @contextmanager
    def _lock(self, path: Path):
        """Acquire an exclusive lock guarding `path` for the duration
        of the with-block.

        The lock is taken on a SIDECAR `<path>.lock` file, never on the
        data file itself. The data file is rotated by `os.replace`
        (atomic-rename pattern), which swaps the inode — flock holders
        on the old inode are no longer mutually exclusive with new
        writers. The sidecar file is stable across replaces, so locks
        on it serialise correctly.
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @contextmanager
    def _yaml_lock(self):
        with self._lock(self._yaml_path):
            yield

    @contextmanager
    def _state_lock(self):
        with self._lock(self._state_path):
            yield

    def _read_yaml(self) -> dict:
        with self._yaml_lock():
            return self._read_yaml_unlocked()

    def _read_yaml_unlocked(self) -> dict:
        if not self._yaml_path.exists():
            return {}
        try:
            data = yaml.safe_load(self._yaml_path.read_text()) or {}
        except Exception as e:
            log.error("[routines_store] yaml parse failed: %s", e)
            return {}
        # Accept either {routines: {id: {...}}} (canonical) or a flat
        # {id: {...}} mapping (tolerant of hand-written files).
        if isinstance(data, dict) and "routines" in data:
            inner = data.get("routines")
            return inner if isinstance(inner, dict) else {}
        return data if isinstance(data, dict) else {}

    def _write_yaml_unlocked(self, defs: dict) -> None:
        # sort_keys=False so we preserve the user's insertion order
        # within an entry (agent_id first, then schedule, then prompt
        # — much easier to scan).
        wrapped = {"routines": defs}
        tmp = self._yaml_path.with_suffix(self._yaml_path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(wrapped, sort_keys=False, allow_unicode=True))
        os.replace(tmp, self._yaml_path)

    def _read_state(self) -> dict:
        with self._state_lock():
            return self._read_state_unlocked()

    def _read_state_unlocked(self) -> dict:
        if not self._state_path.exists():
            return {}
        try:
            return json.loads(self._state_path.read_text())
        except Exception:
            return {}

    def _write_state_unlocked(self, states: dict) -> None:
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(states, indent=2, sort_keys=True))
        os.replace(tmp, self._state_path)

    def _read_runs_unlocked(self) -> dict:
        if not self._runs_path.exists():
            return {}
        try:
            return json.loads(self._runs_path.read_text())
        except Exception:
            return {}

    def _write_runs_unlocked(self, blob: dict) -> None:
        tmp = self._runs_path.with_suffix(self._runs_path.suffix + ".tmp")
        tmp.write_text(json.dumps(blob, indent=2, sort_keys=True))
        os.replace(tmp, self._runs_path)
