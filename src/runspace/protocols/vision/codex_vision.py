"""CodexVision — production Vision impl via Router / gpt-5.3-codex."""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

import httpx
from PIL import Image

from .protocol import Vision

AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://router.example.com/v1")
CODEX_MODEL = os.environ.get("CODEX_MODEL", "gpt-5.4-codex")


def _resize_to_jpeg(image_bytes: bytes, max_side: int = 1024, quality: int = 80) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _looks_like_pdf(data: bytes) -> bool:
    """PDFs start with %PDF-. Tolerate a small leading BOM/whitespace
    that some scanners prepend."""
    return data[:1024].lstrip()[:5] == b"%PDF-"


def _rasterize_pdf_page1(pdf_bytes: bytes, dpi: int = 200) -> bytes:
    """Render page 1 of a PDF to PNG bytes via PyMuPDF.

    Used when callers hand us a scanned-image PDF (acme receipt
    pattern) — the LLM vision endpoint takes images, so the alternative
    is making the user "export to image PDF" by hand. Page 1 is enough
    for restaurant invoices, which are essentially always single-page;
    multi-page support can grow as needed.

    DPI is the rendering resolution before our 1024-px thumbnail
    re-resize. 200 dpi is the sweet spot — sharp enough that small text
    survives the JPEG compression, low enough that a 600-dpi marketing
    PDF doesn't blow up RAM before the thumbnail kicks in.
    """
    import fitz  # PyMuPDF — already pinned, no new dep

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.page_count == 0:
            raise ValueError("PDF has zero pages")
        page = doc.load_page(0)
        zoom = dpi / 72.0  # 72 dpi is fitz's default
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return pix.tobytes("png")


def _to_image_bytes(image: Path | bytes) -> bytes:
    """Return raster image bytes the codex endpoint can ingest.

    Accepts either an image (JPG/PNG/etc.) or a PDF; rasterizes the
    PDF's first page if needed. Caller still gets passed through
    `_resize_to_jpeg` afterwards for the 1024-px / q80 JPEG required
    by the codex vision endpoint.
    """
    if isinstance(image, (str, Path)):
        raw = Path(image).read_bytes()
    else:
        raw = image
    if _looks_like_pdf(raw):
        return _rasterize_pdf_page1(raw)
    return raw


def _resolve_key() -> str:
    for env_var in ("VISION_API_KEY", "AI_API_KEY"):
        v = os.environ.get(env_var)
        if v:
            return v.strip()
    raise RuntimeError("VISION_API_KEY or AI_API_KEY must be set")


class CodexVision(Vision):
    """gpt-5.3-codex multimodal extraction over Router."""

    def __init__(self, *, model: str | None = None, timeout: float = 180):
        self.model = model or CODEX_MODEL
        self.timeout = timeout

    async def extract(
        self, image: Path | bytes, prompt: str, *, fields: list[str] | None = None
    ) -> dict:
        raw_bytes = _to_image_bytes(image)
        jpeg = _resize_to_jpeg(raw_bytes)
        b64 = base64.b64encode(jpeg).decode()
        instruction = (
            "Extract requested fields. Output strict JSON only, no markdown, no commentary."
        )
        if fields:
            instruction += f" Required keys: {', '.join(fields)}."
        body = {
            "model": self.model,
            "instructions": instruction,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
                    ],
                }
            ],
            "store": False,
            "stream": True,  # Router requires this
        }
        text = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{AI_BASE_URL}/codex/responses",
                json=body,
                headers={"Authorization": f"Bearer {_resolve_key()}"},
            ) as r:
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        chunk = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("type") == "response.output_text.delta":
                        text += chunk.get("delta", "")
        # Try to parse JSON from the model output. Models sometimes wrap
        # in ```json fences; strip if present.
        cleaned = text.strip().lstrip("`").lstrip("json").strip("`").strip()
        try:
            extracted = json.loads(cleaned)
        except json.JSONDecodeError:
            # Fall back: return raw with confidence = 0 so callers
            # know to put it in review.
            return {"extracted": {}, "confidence": 0.0, "raw": text}
        return {"extracted": extracted, "confidence": 0.85, "raw": text}
