"""Activity audit log — tracks every agent action.

Generic, reusable across any workspace deployment.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ActivityEntry:
    timestamp: float
    actor: str
    actor_name: str
    action: str
    detail: str
    entity_type: str = ""
    entity_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.timestamp))
        return d


class ActivityLog:
    """In-memory activity log. Can be subclassed for DB persistence."""

    def __init__(self, max_entries: int = 500):
        self._log: list[ActivityEntry] = []
        self._max = max_entries

    def log(
        self,
        actor: str,
        actor_name: str,
        action: str,
        detail: str,
        entity_type: str = "",
        entity_id: str = "",
        metadata: dict | None = None,
    ) -> ActivityEntry:
        entry = ActivityEntry(
            timestamp=time.time(),
            actor=actor,
            actor_name=actor_name,
            action=action,
            detail=detail,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata or {},
        )
        self._log.append(entry)
        if len(self._log) > self._max:
            del self._log[: len(self._log) - self._max]
        return entry

    def query(
        self, limit: int = 50, actor: str | None = None, action: str | None = None
    ) -> list[dict]:
        entries = self._log
        if actor:
            entries = [e for e in entries if e.actor == actor]
        if action:
            entries = [e for e in entries if e.action == action]
        return [e.to_dict() for e in reversed(entries)][:limit]

    def clear(self) -> None:
        self._log.clear()
