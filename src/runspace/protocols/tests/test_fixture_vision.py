"""Tests for FixtureVision — sandbox-mode Vision behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runspace.protocols.vision import FixtureVision, Vision


@pytest.fixture
def vision_root(tmp_path) -> Path:
    return tmp_path


@pytest.fixture
def vision(vision_root) -> FixtureVision:
    return FixtureVision(vision_root)


def test_implements_vision_protocol(vision):
    assert isinstance(vision, Vision)


@pytest.mark.asyncio
async def test_returns_fixture_when_present(vision, vision_root):
    (vision_root / "acme.json").write_text(
        json.dumps(
            {
                "supplier": "Acme_Supplies_Ltd",
                "total_amount": "103.94",
                "currency": "EUR",
            }
        ),
        encoding="utf-8",
    )
    out = await vision.extract(Path("/some/path/acme.pdf"), "extract invoice fields")
    assert out["extracted"]["supplier"] == "Acme_Supplies_Ltd"
    assert out["confidence"] == 1.0
    assert "fixture://" in out["raw"]


@pytest.mark.asyncio
async def test_full_envelope_passthrough(vision, vision_root):
    """If a fixture is already in {extracted, confidence, raw} shape,
    return it verbatim (lets tests script low-confidence cases)."""
    (vision_root / "blurry.json").write_text(
        json.dumps(
            {
                "extracted": {"supplier": "?"},
                "confidence": 0.3,
                "raw": "manual review needed",
            }
        ),
        encoding="utf-8",
    )
    out = await vision.extract(Path("/x/blurry.jpg"), "extract")
    assert out["confidence"] == 0.3
    assert out["raw"] == "manual review needed"


@pytest.mark.asyncio
async def test_missing_fixture_returns_zero_confidence(vision):
    """No fixture → confidence 0 so caller can route to review queue,
    not crash."""
    out = await vision.extract(Path("/x/never_seen.pdf"), "extract")
    assert out["confidence"] == 0.0
    assert out["extracted"] == {}
    assert "no fixture" in out["raw"]


@pytest.mark.asyncio
async def test_corrupted_fixture_returns_zero_confidence(vision, vision_root):
    (vision_root / "broken.json").write_text("not json {{{", encoding="utf-8")
    out = await vision.extract(Path("/x/broken.png"), "extract")
    assert out["confidence"] == 0.0
    assert "not valid JSON" in out["raw"]


@pytest.mark.asyncio
async def test_bytes_input_unsupported_in_sandbox(vision):
    """FixtureVision is filename-keyed by design. Bytes input has no
    name, so we degrade gracefully (confidence 0) rather than guess."""
    out = await vision.extract(b"\xff\xd8\xff\xe0...JPEG_BYTES", "extract")
    assert out["confidence"] == 0.0
    assert "needs a Path" in out["raw"]
