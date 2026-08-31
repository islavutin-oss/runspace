"""FileStorage — the 5th adapter protocol."""

from __future__ import annotations

from .protocol import FileMetadata, FileStorage

try:
    from .local import LocalFileStorage
except ImportError:  # pragma: no cover
    LocalFileStorage = None  # type: ignore[assignment]

__all__ = ["FileMetadata", "FileStorage", "LocalFileStorage"]
