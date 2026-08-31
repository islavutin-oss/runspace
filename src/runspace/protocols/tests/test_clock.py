"""Tests for the Clock primitive — time-travel testing for date-dependent code."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from runspace.protocols import clock as clock_mod
from runspace.protocols.clock import Clock, FrozenClock, RealClock, get_clock


@pytest.fixture(autouse=True)
def _reset_around():
    clock_mod.reset()
    yield
    clock_mod.reset()


# ── FrozenClock ────────────────────────────────────────────────────────
class TestFrozenClock:
    def test_implements_clock_protocol(self):
        assert isinstance(FrozenClock("2026-04-30"), Clock)

    def test_today_returns_frozen(self):
        assert FrozenClock("2026-04-30").today() == date(2026, 4, 30)

    def test_now_returns_midnight_utc_by_default(self):
        c = FrozenClock("2026-04-30")
        assert c.now() == datetime(2026, 4, 30, 0, 0, 0, tzinfo=clock_mod.timezone.utc)

    def test_now_with_explicit_time(self):
        c = FrozenClock("2026-04-30", time_str="15:30:00")
        assert c.now().hour == 15
        assert c.now().minute == 30

    def test_accepts_date_object(self):
        c = FrozenClock(date(2026, 5, 1))
        assert c.today() == date(2026, 5, 1)

    def test_repeated_calls_return_same_value(self):
        """A frozen clock must NOT advance — even if called many times,
        across system clock changes, etc."""
        c = FrozenClock("2026-04-30")
        first = c.today()
        # System time advances around us; FrozenClock doesn't care.
        assert c.today() == first
        assert c.today() == first


# ── RealClock ──────────────────────────────────────────────────────────
class TestRealClock:
    def test_implements_clock_protocol(self):
        assert isinstance(RealClock(), Clock)

    def test_today_is_a_date(self):
        out = RealClock().today()
        assert isinstance(out, date)

    def test_now_is_utc(self):
        out = RealClock().now()
        assert out.tzinfo is not None


# ── get_clock factory ──────────────────────────────────────────────────
class TestGetClock:
    def test_default_mode_returns_real_clock(self, monkeypatch):
        monkeypatch.delenv("APP_MODE", raising=False)
        monkeypatch.delenv("DEMO_TODAY", raising=False)
        clock_mod.reset()
        assert isinstance(get_clock(), RealClock)

    def test_sandbox_mode_with_demo_today_returns_frozen(self, monkeypatch):
        monkeypatch.setenv("APP_MODE", "sandbox")
        monkeypatch.setenv("DEMO_TODAY", "2026-04-30")
        clock_mod.reset()
        c = get_clock()
        assert isinstance(c, FrozenClock)
        assert c.today() == date(2026, 4, 30)

    def test_sandbox_without_demo_today_falls_back_to_real(self, monkeypatch):
        """Defensive: sandbox mode that forgets DEMO_TODAY should not crash;
        return RealClock so tests don't silently break on Pacific time zones."""
        monkeypatch.setenv("APP_MODE", "sandbox")
        monkeypatch.delenv("DEMO_TODAY", raising=False)
        clock_mod.reset()
        assert isinstance(get_clock(), RealClock)

    def test_factory_caches_across_calls(self, monkeypatch):
        monkeypatch.setenv("APP_MODE", "sandbox")
        monkeypatch.setenv("DEMO_TODAY", "2026-04-30")
        clock_mod.reset()
        a, b = get_clock(), get_clock()
        assert a is b

    def test_reset_breaks_cache(self, monkeypatch):
        monkeypatch.setenv("APP_MODE", "sandbox")
        monkeypatch.setenv("DEMO_TODAY", "2026-04-30")
        clock_mod.reset()
        a = get_clock()
        clock_mod.reset()
        b = get_clock()
        assert a is not b


# ── Real-world usage: domain code that branches on today ──────────────
def _is_overdue(due_str: str, *, clock: Clock) -> bool:
    """Sample domain function — uses Clock to decide overdue."""
    return date.fromisoformat(due_str) < clock.today()


class TestDomainUsage:
    """Show how Clock makes date-branching code testable."""

    def test_overdue_against_frozen_today(self):
        clock = FrozenClock("2026-04-30")
        assert _is_overdue("2026-04-29", clock=clock) is True
        assert _is_overdue("2026-04-30", clock=clock) is False
        assert _is_overdue("2026-05-01", clock=clock) is False

    def test_can_simulate_three_days_in_the_future(self, monkeypatch):
        """The reason this primitive exists: «what would Ada say in 3 days?».
        No test container time-travel, no freezegun magic — one fixture."""
        monkeypatch.setenv("APP_MODE", "sandbox")

        # Today: 2026-04-30; nothing overdue
        monkeypatch.setenv("DEMO_TODAY", "2026-04-30")
        clock_mod.reset()
        c = get_clock()
        assert _is_overdue("2026-05-02", clock=c) is False

        # Three days later: same invoice IS overdue
        monkeypatch.setenv("DEMO_TODAY", "2026-05-03")
        clock_mod.reset()
        c = get_clock()
        assert _is_overdue("2026-05-02", clock=c) is True

    def test_weekend_boundary(self):
        """A real bug class: «if today is Friday, send the digest;
        otherwise wait». This is impossible to test against wall clock;
        trivial with FrozenClock."""
        friday = FrozenClock("2026-05-01")  # Friday
        saturday = FrozenClock("2026-05-02")
        assert friday.today().weekday() == 4  # Mon=0..Sun=6, Fri=4
        assert saturday.today().weekday() == 5
