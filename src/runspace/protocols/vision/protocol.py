"""Vision protocol — image / PDF page → structured fields."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Vision(Protocol):
    """Extract structured fields from a scanned image or rasterized PDF page."""

    async def extract(
        self, image: Path | bytes, prompt: str, *, fields: list[str] | None = None
    ) -> dict:
        """Run the model on the image and return structured output.

        Args:
            image: file path or raw bytes (JPEG/PNG).
            prompt: natural-language instruction (what to extract).
            fields: optional list of field names that the impl should
                    coerce-into-keys. Useful when callers want a stable
                    JSON shape; impls may ignore.

        Returns:
            dict with `extracted`, `confidence`, `raw` at minimum.
        """
        ...
