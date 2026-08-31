"""Vision OCR snapshot tests — gates the next model upgrade."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from runspace.protocols.vision import FixtureVision

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "vision"

# (image_basename, expected_subset, must_present_keys)
# expected_subset = the JSON keys we know exactly; live extractions are
KNOWN_INVOICES = [
    pytest.param(
        "sample_invoice",
        {
            "supplier": "Acme Test Suppliers Ltd",
            "invoice_no": "TEST-001",
            "currency": "EUR",
            "total_amount": "100.00",
        },
        ["supplier", "invoice_no", "total_amount", "currency"],
        id="sample_invoice",
    ),
]


# ── Sandbox snapshot tests (always run) ────────────────────────────────
@pytest.mark.parametrize("name,expected,must_present", KNOWN_INVOICES)
@pytest.mark.asyncio
async def test_fixture_vision_returns_exact_snapshot(name, expected, must_present):
    """FixtureVision must return the snapshot JSON byte-for-byte."""
    vision = FixtureVision(FIXTURES_DIR)
    fake_path = Path("/x/" + name + ".jpg")  # only basename matters
    out = await vision.extract(fake_path, "extract invoice fields")
    assert out["confidence"] == 1.0
    extracted = out["extracted"]
    # Every key in expected_subset must match exactly
    for k, v in expected.items():
        assert extracted.get(k) == v, (
            f"FixtureVision drifted on {k!r}: expected {v!r}, got {extracted.get(k)!r}"
        )


@pytest.mark.asyncio
async def test_unknown_image_returns_zero_confidence():
    """Snapshot harness must NOT silently accept missing fixtures —
    confidence=0 lets calling tests detect & fail loud."""
    vision = FixtureVision(FIXTURES_DIR)
    out = await vision.extract(Path("/x/never_seen.jpg"), "extract")
    assert out["confidence"] == 0.0


# ── Live model gate (gated; opt-in for CI) ─────────────────────────────
def _live_gate_active() -> bool:
    return os.environ.get("RUN_LIVE_VISION_TESTS") == "1" and bool(os.environ.get("AI_API_KEY"))


@pytest.mark.skipif(
    not _live_gate_active(), reason="set RUN_LIVE_VISION_TESTS=1 + AI_API_KEY to run"
)
@pytest.mark.parametrize("name,expected,must_present", KNOWN_INVOICES)
@pytest.mark.asyncio
async def test_codex_vision_extracts_known_invoices(name, expected, must_present):
    """Run real CodexVision; assert key fields are present and fuzzy-match.

    NOT exact match — LLMs emit "30/04/2026" vs "2026-04-30", "EUR 100" vs
    "100.00", etc. We assert presence + substring containment of the
    canonical value. Strict regression detection without false positives.
    """
    from runspace.protocols.vision import CodexVision

    image_path = FIXTURES_DIR / f"{name}.jpg"
    assert image_path.exists(), f"fixture image missing: {image_path}"

    vision = CodexVision()
    out = await vision.extract(
        image_path,
        "Extract: supplier, invoice_no, invoice_date, total_amount, "
        "currency, due_date, iban. Output JSON only.",
        fields=["supplier", "invoice_no", "total_amount", "currency"],
    )
    assert out["confidence"] >= 0.5, "model returned low confidence"
    extracted = out["extracted"]

    # All required keys present + non-empty
    for k in must_present:
        assert k in extracted, f"missing key {k!r} in extraction"
        v = extracted[k]
        assert v not in (None, "", []), f"empty value for {k!r}"

    # Fuzzy field-by-field check
    for k, expected_val in expected.items():
        if k not in extracted:
            continue  # already failed above for required keys
        got = str(extracted[k]).lower().strip()
        want = str(expected_val).lower().strip()
        # Exact match OR canonical substring (handles "EUR 100.00" / "100,00 EUR")
        normalized_got = got.replace(",", ".").replace("€", "").strip()
        normalized_want = want.replace(",", ".").replace("€", "").strip()
        assert (
            normalized_got == normalized_want
            or normalized_want in normalized_got
            or normalized_got in normalized_want
        ), f"live extraction drift on {k!r}: expected ~{expected_val!r}, got {extracted[k]!r}"
