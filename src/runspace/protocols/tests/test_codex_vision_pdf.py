"""CodexVision must accept scanned-image PDFs without forcing the user to "export as image PDF" first."""

from __future__ import annotations

from runspace.protocols.vision.codex_vision import (
    _looks_like_pdf,
    _rasterize_pdf_page1,
    _to_image_bytes,
)


def _build_one_page_pdf() -> bytes:
    """Build a tiny one-page PDF in memory with a label, to verify
    the rasterizer produces an image we can subsequently feed PIL.
    Uses fitz directly so the test isn't tied to any pre-baked file."""
    import fitz

    doc = fitz.open()  # empty
    page = doc.new_page(width=400, height=200)
    page.insert_text((50, 100), "Acme_Supplies_Ltd — €1,234.56")
    out = doc.tobytes()
    doc.close()
    return out


def test_looks_like_pdf_recognises_real_pdf():
    pdf = _build_one_page_pdf()
    assert _looks_like_pdf(pdf)


def test_looks_like_pdf_rejects_jpeg():
    # JPEG magic
    jpeg_header = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
    assert not _looks_like_pdf(jpeg_header)


def test_looks_like_pdf_rejects_png():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    assert not _looks_like_pdf(png_header)


def test_rasterize_pdf_page1_yields_valid_image():
    pdf = _build_one_page_pdf()
    png_bytes = _rasterize_pdf_page1(pdf)
    # PIL must be able to open the result — that's the whole point.
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes))
    assert img.size[0] > 0
    assert img.size[1] > 0


def test_to_image_bytes_routes_pdf_through_rasterizer(tmp_path):
    """End-to-end at the helper level: hand `_to_image_bytes` a real
    PDF, get back image bytes that PIL can read. Without the
    rasterization branch, this returns raw `%PDF-...` bytes and the
    downstream `_resize_to_jpeg` call (PIL.Image.open) explodes —
    exactly the production bug."""
    pdf = _build_one_page_pdf()
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(pdf)

    # Both code paths (Path and raw bytes) should produce image bytes.
    for input_form in (pdf_path, pdf):
        out = _to_image_bytes(input_form)
        assert not _looks_like_pdf(out), (
            "PDF leaked through unrasterized — `_resize_to_jpeg` will "
            "raise on this. See 2026-05-02 acme regression."
        )
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(out))
        assert img.size[0] > 0


def test_to_image_bytes_passes_jpeg_through_unchanged(tmp_path):
    """Plain images must NOT be rasterized — that would corrupt them."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (100, 50), color=(255, 0, 0)).save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()
    out = _to_image_bytes(jpeg_bytes)
    assert out == jpeg_bytes
