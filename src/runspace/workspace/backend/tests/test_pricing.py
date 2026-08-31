"""Tests for the cost ledger (pricing.py)."""

from __future__ import annotations

from runspace.workspace.backend.pricing import (
    DEFAULTS,
    Price,
    cost_cents,
    cost_cents_from_trace_events,
    lookup,
)


def test_known_model_returns_price():
    p = lookup("gpt-5.3-codex")
    assert isinstance(p, Price)
    assert p.input_per_m > 0


def test_unknown_model_returns_none():
    assert lookup("nonexistent-model-xyz") is None
    assert lookup(None) is None


def test_prefix_match_for_thinking_suffixes():
    """`gpt-5.3-codex:high` should price as `gpt-5.3-codex`."""
    assert lookup("gpt-5.3-codex:high") == DEFAULTS["gpt-5.3-codex"]


def test_env_override(monkeypatch):
    monkeypatch.setenv("AGENTINO_PRICE_OVERRIDE_GPT_5_3_CODEX", "1.0,2.0")
    p = lookup("gpt-5.3-codex")
    assert p.input_per_m == 1.0
    assert p.output_per_m == 2.0


def test_cost_cents_basic():
    # 1M input @ $2 = $2 = 200 cents
    cents = cost_cents("gpt-5.3-codex", 1_000_000, 0)
    assert cents == 200.0
    cents = cost_cents("gpt-5.3-codex", 0, 1_000_000)
    assert cents == 800.0  # output $8/M
    # 30k input + 200 output
    cents = cost_cents("gpt-5.3-codex", 30_000, 200)
    # 30k * 2/M + 200 * 8/M = $0.06 + $0.0016 = $0.0616 = 6.16 cents
    assert abs(cents - 6.16) < 0.01


def test_cost_cents_unknown_model_zero():
    assert cost_cents("not-a-model", 1_000_000, 1_000_000) == 0.0


def test_cost_from_trace_events_aggregates():
    events = [
        {"type": "context"},
        {"type": "llm_response", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
        {"type": "tool_start", "name": "x"},
        {"type": "llm_response", "usage": {"prompt_tokens": 2000, "completion_tokens": 200}},
        {"type": "final"},
    ]
    cents = cost_cents_from_trace_events("gpt-5.3-codex", events)
    # First call: 1000 * 2/M + 100 * 8/M = $0.002 + $0.0008 = $0.0028 = 0.28 cents
    # Second call: 2000 * 2/M + 200 * 8/M = $0.004 + $0.0016 = $0.0056 = 0.56 cents
    # Total ≈ 0.84 cents
    assert abs(cents - 0.84) < 0.01


def test_cost_from_trace_events_skips_unknown_model():
    events = [
        {"type": "llm_response", "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}}
    ]
    assert cost_cents_from_trace_events("not-a-model", events) == 0.0
