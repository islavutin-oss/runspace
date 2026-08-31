"""Clock — small time-travel primitive for testing date-dependent behavior."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def today(self) -> date:
        """Today's date in UTC."""
        ...

    def now(self) -> datetime:
        """UTC datetime, microsecond precision (or impl's best)."""
        ...


class RealClock(Clock):
    """Production Clock — wall-clock UTC."""

    def today(self) -> date:
        return datetime.now(timezone.utc).date()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock(Clock):
    """Test/sandbox Clock — returns a fixed date forever.

    Construct with either an iso date ('2026-04-30') or a date object.
    `now()` returns midnight UTC of that date unless `time` is provided.
    """

    def __init__(self, frozen_date: str | date, *, time_str: str = "00:00:00"):
        if isinstance(frozen_date, str):
            frozen_date = date.fromisoformat(frozen_date)
        self._date = frozen_date
        self._time_str = time_str

    def today(self) -> date:
        return self._date

    def now(self) -> datetime:
        return datetime.fromisoformat(f"{self._date.isoformat()}T{self._time_str}+00:00")


@lru_cache(maxsize=1)
def get_clock() -> Clock:
    """APP_MODE-gated factory. In sandbox mode, looks for DEMO_TODAY env;
    falls back to RealClock if unset (rare — tests should set DEMO_TODAY
    explicitly via the frozen_clock fixture)."""
    mode = os.environ.get("APP_MODE", "live")
    if mode == "sandbox":
        demo = os.environ.get("DEMO_TODAY")
        if demo:
            return FrozenClock(demo)
    return RealClock()


def reset() -> None:
    """Clear the lru_cache. Call from tests when changing DEMO_TODAY mid-process."""
    get_clock.cache_clear()
