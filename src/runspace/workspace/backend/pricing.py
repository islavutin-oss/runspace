"""Token-based cost ledger."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """USD per 1 million tokens."""

    input_per_m: float
    output_per_m: float


# Defaults — feel free to update. The dashboard's settings JSON can also
# carry overrides per-project (future work; for now env var is the lever).
DEFAULTS: dict[str, Price] = {
    # OpenAI / Codex via Router (subscription — these are the
    # equivalent metered prices for cost-comparison purposes; actual
    # billing may differ if the user is on a flat-rate plan).
    "gpt-5.3-codex": Price(input_per_m=2.00, output_per_m=8.00),
    "gpt-5.4": Price(input_per_m=1.25, output_per_m=10.00),
    "gpt-5.4-codex": Price(input_per_m=1.25, output_per_m=10.00),
    "gpt-4.1-2025-04-14": Price(input_per_m=2.00, output_per_m=8.00),
    "gpt-4o": Price(input_per_m=2.50, output_per_m=10.00),
    # Anthropic
    "claude-haiku-4-5-20251001": Price(input_per_m=1.00, output_per_m=5.00),
    "claude-haiku-4-5": Price(input_per_m=1.00, output_per_m=5.00),
    "claude-sonnet-4-5": Price(input_per_m=3.00, output_per_m=15.00),
    "claude-sonnet-4-6": Price(input_per_m=3.00, output_per_m=15.00),
    "claude-opus-4-7": Price(input_per_m=15.00, output_per_m=75.00),
    # Open / cheap
    "google/gemma-4-E4B-it": Price(input_per_m=0.10, output_per_m=0.30),
    "qwen/qwen3.6-plus": Price(input_per_m=0.40, output_per_m=1.20),
}


def lookup(model: str | None) -> Price | None:
    if not model:
        return None
    # Env override: AGENTINO_PRICE_OVERRIDE_GPT_5_3_CODEX="2.0,8.0"
    safe = (model or "").upper().replace("/", "_").replace("-", "_").replace(".", "_")
    override = os.environ.get(f"AGENTINO_PRICE_OVERRIDE_{safe}")
    if override:
        try:
            inp, out = override.split(",", 1)
            return Price(input_per_m=float(inp), output_per_m=float(out))
        except (ValueError, IndexError):
            pass
    p = DEFAULTS.get(model)
    if p:
        return p
    # Tolerate trivial prefix variants ("gpt-5.3-codex:high" → "gpt-5.3-codex")
    for k in DEFAULTS:
        if model.startswith(k):
            return DEFAULTS[k]
    return None


def cost_cents(model: str | None, prompt_tokens: int, completion_tokens: int) -> float:
    """Return cost in cents (so the dashboard can format $0.0123 cleanly)."""
    p = lookup(model)
    if p is None:
        return 0.0
    dollars = (prompt_tokens / 1_000_000.0) * p.input_per_m + (
        completion_tokens / 1_000_000.0
    ) * p.output_per_m
    return round(dollars * 100.0, 4)


def cost_cents_from_trace_events(model: str | None, events: list[dict]) -> float:
    """Sum cost across all `llm_response` events in a trial trace."""
    total = 0.0
    for ev in events:
        if ev.get("type") != "llm_response":
            continue
        u = ev.get("usage") or {}
        total += cost_cents(
            model,
            int(u.get("prompt_tokens") or 0),
            int(u.get("completion_tokens") or 0),
        )
    return round(total, 4)
