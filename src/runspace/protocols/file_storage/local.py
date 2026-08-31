"""LocalFileStorage — filesystem-backed FileStorage impl."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .protocol import FileMetadata

_NAME_OK = re.compile(r"^[A-Za-z0-9._\-]+$")


def _safe_tenant(tenant_id: str) -> str:
    """Reject anything that could escape the root."""
    if (
        not tenant_id
        or "/" in tenant_id
        or "\\" in tenant_id
        or "\x00" in tenant_id
        or tenant_id in ("..", ".")
        or tenant_id.startswith(".")
    ):
        raise ValueError(f"invalid tenant_id {tenant_id!r}")
    return tenant_id


def _safe_file_id(file_id: str) -> str:
    """File ids are storage-layer-assigned (we control them). Validate
    anyway so a tampered persistence layer can't escape the dir."""
    if not file_id or not _NAME_OK.match(file_id):
        raise ValueError(f"invalid file_id {file_id!r}")
    return file_id


def _sanitize_name(name: str) -> str:
    """Strip path separators + unsafe chars from the original name so
    we can include it in the file_id for human readability."""
    base = Path(name).name  # drop any directory components
    base = base.replace(" ", "_")
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base[:80] or "file"


class LocalFileStorage:
    """Filesystem-backed FileStorage."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ── tenant dir helpers ─────────────────────────────────────────────
    def _tenant_dir(self, tenant_id: str) -> Path:
        d = self.root / _safe_tenant(tenant_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _file_path(self, tenant_id: str, file_id: str) -> Path:
        return self._tenant_dir(tenant_id) / _safe_file_id(file_id)

    def _meta_path(self, tenant_id: str, file_id: str) -> Path:
        return self._file_path(tenant_id, file_id).with_suffix(
            self._file_path(tenant_id, file_id).suffix + ".json"
        )

    # ── FileStorage protocol ──────────────────────────────────────────
    def put(
        self,
        tenant_id: str,
        original_name: str,
        content: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> FileMetadata:
        sha = hashlib.sha256(content).hexdigest()
        token = secrets.token_hex(6)  # 12 hex chars, mirrors gateway convention
        safe = _sanitize_name(original_name)
        file_id = f"{token}_{safe}"

        path = self._file_path(tenant_id, file_id)
        path.write_bytes(content)

        meta = FileMetadata(
            file_id=file_id,
            tenant_id=tenant_id,
            original_name=original_name,
            size_bytes=len(content),
            content_type=content_type,
            created_at=datetime.now(timezone.utc).isoformat(),
            sha256=sha,
        )
        self._meta_path(tenant_id, file_id).write_text(
            json.dumps(meta.__dict__, ensure_ascii=False, indent=2)
        )
        return meta

    def get(self, tenant_id: str, file_id: str) -> bytes:
        path = self._file_path(tenant_id, file_id)
        if not path.exists():
            raise FileNotFoundError(f"file {file_id!r} not found for tenant {tenant_id!r}")
        return path.read_bytes()

    def metadata(self, tenant_id: str, file_id: str) -> FileMetadata:
        meta_path = self._meta_path(tenant_id, file_id)
        if not meta_path.exists():
            raise FileNotFoundError(f"file {file_id!r} not found for tenant {tenant_id!r}")
        data = json.loads(meta_path.read_text())
        return FileMetadata(**data)

    def list(self, tenant_id: str) -> list[FileMetadata]:
        d = self._tenant_dir(tenant_id)
        out: list[FileMetadata] = []
        for meta_path in sorted(d.glob("*.json")):
            try:
                data = json.loads(meta_path.read_text())
                out.append(FileMetadata(**data))
            except (json.JSONDecodeError, TypeError):
                continue  # skip malformed meta sidecars
        return out

    def delete(self, tenant_id: str, file_id: str) -> bool:
        path = self._file_path(tenant_id, file_id)
        meta_path = self._meta_path(tenant_id, file_id)
        existed = path.exists() or meta_path.exists()
        for p in (path, meta_path):
            if p.exists():
                p.unlink()
        return existed

    def signed_url(self, tenant_id: str, file_id: str, ttl_seconds: int = 300) -> str:
        """Local impl returns a tenant-scoped relative path. The gateway
        decides how to surface it (proxy via API, mount at a static
        path). TTL is ignored — local storage doesn't expire URLs.
        """
        return f"/api/files/{_safe_tenant(tenant_id)}/{_safe_file_id(file_id)}"
