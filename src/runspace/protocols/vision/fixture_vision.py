"""FixtureVision — sandbox Vision impl."""

from __future__ import annotations

import json
from pathlib import Path

from .protocol import Vision


class FixtureVision(Vision):
    """Filename-keyed scripted Vision impl for sandbox mode."""

    def __init__(self, fixtures_dir: Path | str):
        self.fixtures_dir = Path(fixtures_dir)

    async def extract(
        self, image: Path | bytes, prompt: str, *, fields: list[str] | None = None
    ) -> dict:
        if isinstance(image, bytes):
            return {
                "extracted": {},
                "confidence": 0.0,
                "raw": "FixtureVision needs a Path, got bytes",
            }
        stem = Path(image).stem
        fixture = self.fixtures_dir / f"{stem}.json"
        if not fixture.exists():
            return {
                "extracted": {},
                "confidence": 0.0,
                "raw": f"no fixture for {stem} at {fixture}",
            }
        try:
            data = json.loads(fixture.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return {
                "extracted": {},
                "confidence": 0.0,
                "raw": f"fixture {fixture} is not valid JSON: {e}",
            }
        # Two shapes accepted:
        #   1. raw extraction dict: {"supplier": "...", "total": 100}
        #   2. full envelope: {"extracted": {...}, "confidence": 0.95}
        if "extracted" in data and "confidence" in data:
            return data
        return {"extracted": data, "confidence": 1.0, "raw": f"fixture://{stem}"}
