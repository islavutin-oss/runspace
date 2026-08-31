"""Regression: scanned-image PDFs must NOT be flagged as "Empty PDF" in the chat context, otherwise the LLM concludes the file has no content and refuses to call the OCR/vision tool."""

from __future__ import annotations

from runspace.workspace.backend.file_extractors import _read_file_content


def _build_scanned_pdf() -> bytes:
    """Build a one-page PDF that has only a rasterized image (no text
    layer). Mimics CamScanner / camera-photo PDFs."""
    import io

    import fitz
    from PIL import Image

    img_buf = io.BytesIO()
    Image.new("RGB", (200, 200), color=(180, 180, 180)).save(img_buf, format="PNG")
    img_buf.seek(0)
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_image(page.rect, stream=img_buf.getvalue())
    out = doc.tobytes()
    doc.close()
    return out


def _build_text_pdf() -> bytes:
    """Build a PDF with a real text layer — should still extract the text."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((50, 100), "Invoice 8021737")
    out = doc.tobytes()
    doc.close()
    return out


def test_scanned_pdf_does_not_say_empty():
    """The killer regression: a PDF whose pages have no text layer
    must not produce a context line saying "Empty PDF" — that
    misleads the LLM into refusing to act."""
    pdf = _build_scanned_pdf()
    out = _read_file_content(pdf, "application/pdf", "scan.pdf", len(pdf))
    assert "Empty PDF" not in out, (
        f"Scanned-image PDFs must not be reported as empty in the chat context. Got: {out!r}"
    )


def test_scanned_pdf_points_at_ocr_tool():
    """The hint must direct the LLM to the OCR tool, not just say
    "no text" (which the LLM would still interpret as "useless file")."""
    pdf = _build_scanned_pdf()
    out = _read_file_content(pdf, "application/pdf", "scan.pdf", len(pdf))
    # Mention either OCR or vision — LLM needs a clue
    lowered = out.lower()
    assert "ocr" in lowered or "vision" in lowered, (
        f"Hint missing OCR/vision keyword to nudge the LLM: {out!r}"
    )
    # Process_invoice is the typical entrypoint for scanned invoices
    assert "process_invoice" in out


def test_text_pdf_returns_actual_text():
    """Text-layer PDFs must continue to return their actual content
    — the new branch is only for the no-text case."""
    pdf = _build_text_pdf()
    out = _read_file_content(pdf, "application/pdf", "doc.pdf", len(pdf))
    assert "8021737" in out
    assert "Scanned-image" not in out
