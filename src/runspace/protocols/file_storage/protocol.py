"""FileStorage protocol — contract for binary blob storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FileMetadata:
    """Provenance + addressability for a stored file. Returned by `put`
    so callers can persist the id and surface human-readable info."""

    file_id: str  # opaque, storage-layer-assigned
    tenant_id: str  # multi-tenant scope key
    original_name: str  # what the user uploaded as
    size_bytes: int
    content_type: str  # best-effort MIME from the caller
    created_at: str  # ISO 8601 UTC
    sha256: str  # full SHA-256 hex digest of the bytes


class FileStorage(Protocol):
    """Adapter contract — every impl satisfies the same surface so
    tools can swap backends via config without code changes.
    """

    def put(
        self,
        tenant_id: str,
        original_name: str,
        content: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> FileMetadata:
        """Store bytes for a tenant. Returns the metadata; the caller
        persists `file_id` if it needs to retrieve later."""
        ...

    def get(self, tenant_id: str, file_id: str) -> bytes:
        """Read bytes back. Raises FileNotFoundError if missing.

        IMPORTANT: passing the wrong tenant_id MUST NOT return the file
        even if the file_id is correct. Tenant scoping is enforced here.
        """
        ...

    def metadata(self, tenant_id: str, file_id: str) -> FileMetadata:
        """Return the metadata without the bytes — useful for listings,
        size checks, content-type sniffing."""
        ...

    def list(self, tenant_id: str) -> list[FileMetadata]:
        """All files for a tenant. Used by the dashboard's "Files" panel,
        bulk-export, etc. Should never include other tenants' files."""
        ...

    def delete(self, tenant_id: str, file_id: str) -> bool:
        """Remove a file. Returns True if deleted, False if missing.
        Idempotent."""
        ...

    def signed_url(self, tenant_id: str, file_id: str, ttl_seconds: int = 300) -> str:
        """Return a URL the browser can use to download the file directly
        without going through the API. For LocalFileStorage this falls
        back to a tenant-scoped relative path that the gateway proxies."""
        ...
