"""File-attachment plumbing for the chat path."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .file_extractors import _read_file_content
from .models import AttachmentInput, FileAttachmentResponse

log = logging.getLogger(__name__)


def _resolve_files(
    file_ids: list[str], attachments: list[AttachmentInput], tenant_id: str | None = None
) -> tuple[str, str | None, str | None]:
    """Resolve all file references into context text + optional audio for transcription.

    Reads through protocols.FileStorage (Supabase Storage or local
    disk depending on config). Tenant scoping is enforced by the storage
    adapter; cross-tenant lookups raise FileNotFoundError.

    Returns (context_text, audio_bytes_b64_or_none, audio_mime_or_none)
    """
    import base64 as b64mod

    parts = []
    audio_bytes = None
    audio_mime = None

    storage = None
    if file_ids:
        from runspace.protocols import get_file_storage

        storage = get_file_storage()

    # Process uploaded files (from /upload endpoint)
    for file_id in file_ids:
        safe_id = Path(file_id).name
        if storage is None or not tenant_id:
            log.warning("FileStorage unavailable or tenant missing for %s", file_id)
            continue
        try:
            raw = storage.get(tenant_id, safe_id)
        except (FileNotFoundError, ValueError):
            log.warning("File not found: %s", file_id)
            continue
        fpath = Path(safe_id)
        # Detect audio files → route to transcription
        mime_map = {
            ".webm": "audio/webm",
            ".wav": "audio/wav",
            ".ogg": "audio/ogg",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/m4a",
            ".flac": "audio/flac",
        }
        suffix = fpath.suffix.lower()
        if suffix in mime_map:
            audio_bytes = b64mod.b64encode(raw).decode()
            audio_mime = mime_map[suffix]
            continue
        # Text/PDF files → extract content
        # Guess MIME from suffix
        mime_text = {
            ".csv": "text/csv",
            ".json": "application/json",
            ".txt": "text/plain",
            ".html": "text/html",
            ".xml": "text/xml",
            ".pdf": "application/pdf",
            ".yaml": "text/yaml",
            ".yml": "text/yaml",
            ".md": "text/markdown",
            ".js": "text/javascript",
            ".py": "text/plain",
            ".ts": "text/plain",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xls": "application/vnd.ms-excel",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        mime = mime_text.get(suffix, "application/octet-stream")
        # Pull the real original_name from storage metadata. Deriving it
        # from the file_id via split("_", 1) leaks the sanitization
        # (spaces → underscores) into the LLM context, so a tool like
        # process_invoice that looks up by `meta.original_name == bare`
        # would never match what the LLM was shown.
        try:
            original_name = storage.metadata(tenant_id, safe_id).original_name
        except Exception:
            # Metadata read failed (concurrent delete, broken json) —
            # fall back to the legacy derivation rather than refusing
            # to attach the file at all.
            original_name = safe_id.split("_", 1)[1] if "_" in safe_id else safe_id
        content = _read_file_content(raw, mime, original_name, len(raw))
        parts.append(f"\n--- Attachment: {original_name} ({mime}, {len(raw)} bytes) ---\n{content}")

    # Process legacy base64 attachments (backward compat)
    for att in attachments:
        content = ""
        if att.content:
            try:
                raw = b64mod.b64decode(att.content)
                content = _read_file_content(raw, att.type, att.name, att.size)
            except Exception:
                content = f"[Could not decode, {att.size} bytes]"
        parts.append(f"\n--- Attachment: {att.name} ({att.type}, {att.size} bytes) ---\n{content}")

    context = "\n".join(parts) if parts else ""
    return context, audio_bytes, audio_mime


def _ensure_attachments_referenced(
    response_text: str, attachments: list[FileAttachmentResponse]
) -> str:
    """If the agent paraphrased ('I attached the PDF') without including
    the markdown link returned by the file-creation tool, the user-visible
    response has nothing to click. Detect that case and append a tiny
    footer with the links.

    Detection: an attachment is "referenced" if either its URL or its
    filename appears verbatim in the response text. Without that, the
    user sees claimed-but-invisible files. We append `📎 [name](url)`
    lines only for attachments that fail the check, so well-formed
    responses (where the LLM did include the link) stay untouched.
    """
    if not attachments:
        return response_text
    missing = []
    for att in attachments:
        if att.url and att.url in response_text:
            continue
        if att.name and att.name in response_text:
            continue
        missing.append(att)
    if not missing:
        return response_text
    footer = "\n\n" + "\n".join(f"📎 [{att.name}]({att.url})" for att in missing)
    return response_text + footer


def _collect_file_attachments(
    text: str, api_prefix: str = "/api/workspace"
) -> list[FileAttachmentResponse]:
    """Scan agent response for `/api/documents/<uuid>/download` URLs and
    return one `FileAttachmentResponse` per unique URL so the UI renders
    download cards.

    The URL itself is permanent + auth-gated; this function just decorates
    it with filename/size/mime by looking up the documents row.

    NOTE — the `api_prefix` parameter is preserved for callsite stability;
    it had a use in the old `/tmp/*.pdf` scan path (now removed) and stays
    so external callers don't break.
    """
    del api_prefix  # legacy parameter, no longer used after dropping /tmp scan
    attachments = []
    seen_doc_ids: set[str] = set()
    doc_url_re = re.compile(
        r"/api/documents/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/download"
    )
    for match in doc_url_re.finditer(text):
        doc_id = match.group(1)
        if doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc_id)
        meta = _lookup_document_meta(doc_id)
        if not meta:
            continue
        attachments.append(
            FileAttachmentResponse(
                name=meta["filename"],
                url=f"/api/documents/{doc_id}/download",
                size=meta["size_bytes"],
                type=meta["mime"],
            )
        )
    return attachments


def _lookup_document_meta(doc_id: str) -> dict | None:
    """Read filename/size/mime for a doc id. Used to decorate attachment cards.

    Service-role read; no tenant check here because the URL itself is auth-gated
    at the proxy endpoint. Worst case: this query returns metadata that the
    user can already see (since they'll only ever see URLs in their own tenant's
    chat).
    """
    try:
        # Go through protocols.Store so sandbox /
        # fixture backends produce the same lookup behaviour.
        from runspace.protocols import get_store

        rec = get_store().get("documents", doc_id)
        if not rec:
            return None
        return {k: rec.get(k) for k in ("filename", "size_bytes", "mime")}
    except Exception:
        return None
