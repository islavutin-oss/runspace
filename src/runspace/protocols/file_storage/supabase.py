"""SupabaseFileStorage — Supabase Storage bucket-backed FileStorage impl."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone

from .local import _safe_file_id, _safe_tenant, _sanitize_name
from .protocol import FileMetadata


class SupabaseFileStorage:
    """Supabase Storage-backed FileStorage."""

    def __init__(
        self, bucket: str = "workspace-files", url: str | None = None, key: str | None = None
    ):
        self.bucket = bucket
        self.url = url or os.environ["SUPABASE_URL"]
        self.key = key or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
        self._client = None  # lazy

    @property
    def client(self):
        if self._client is None:
            from supabase import create_client  # type: ignore

            self._client = create_client(self.url, self.key)
        return self._client

    @property
    def storage(self):
        return self.client.storage.from_(self.bucket)

    # ── path helpers ────────────────────────────────────────────────────
    def _key(self, tenant_id: str, file_id: str) -> str:
        return f"{_safe_tenant(tenant_id)}/{_safe_file_id(file_id)}"

    def _meta_key(self, tenant_id: str, file_id: str) -> str:
        return self._key(tenant_id, file_id) + ".json"

    # ── FileStorage protocol ────────────────────────────────────────────
    def put(
        self,
        tenant_id: str,
        original_name: str,
        content: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> FileMetadata:
        sha = hashlib.sha256(content).hexdigest()
        token = secrets.token_hex(6)
        safe = _sanitize_name(original_name)
        file_id = f"{token}_{safe}"

        # Upload bytes
        self.storage.upload(
            path=self._key(tenant_id, file_id),
            file=content,
            file_options={"content-type": content_type, "upsert": "false"},
        )

        meta = FileMetadata(
            file_id=file_id,
            tenant_id=tenant_id,
            original_name=original_name,
            size_bytes=len(content),
            content_type=content_type,
            created_at=datetime.now(timezone.utc).isoformat(),
            sha256=sha,
        )
        # Sidecar metadata
        self.storage.upload(
            path=self._meta_key(tenant_id, file_id),
            file=json.dumps(meta.__dict__, ensure_ascii=False, indent=2).encode(),
            file_options={"content-type": "application/json", "upsert": "false"},
        )
        return meta

    def get(self, tenant_id: str, file_id: str) -> bytes:
        try:
            return self.storage.download(self._key(tenant_id, file_id))
        except Exception as e:
            raise FileNotFoundError(f"file {file_id!r} not found for tenant {tenant_id!r}") from e

    def metadata(self, tenant_id: str, file_id: str) -> FileMetadata:
        try:
            raw = self.storage.download(self._meta_key(tenant_id, file_id))
        except Exception as e:
            raise FileNotFoundError(f"file {file_id!r} not found for tenant {tenant_id!r}") from e
        return FileMetadata(**json.loads(raw))

    def list(self, tenant_id: str) -> list[FileMetadata]:
        prefix = _safe_tenant(tenant_id) + "/"
        items = self.storage.list(prefix.rstrip("/")) or []
        out: list[FileMetadata] = []
        for entry in items:
            name = entry.get("name", "")
            if not name.endswith(".json"):
                continue  # we only enumerate via meta sidecars
            try:
                file_id = name[: -len(".json")]
                out.append(self.metadata(tenant_id, file_id))
            except FileNotFoundError:
                continue
        return out

    def delete(self, tenant_id: str, file_id: str) -> bool:
        keys = [self._key(tenant_id, file_id), self._meta_key(tenant_id, file_id)]
        try:
            self.storage.remove(keys)
            return True
        except Exception:
            return False

    def signed_url(self, tenant_id: str, file_id: str, ttl_seconds: int = 300) -> str:
        result = self.storage.create_signed_url(self._key(tenant_id, file_id), ttl_seconds)
        # supabase-py shape varies by version; tolerate either
        return result.get("signedURL") or result.get("signed_url") or ""
