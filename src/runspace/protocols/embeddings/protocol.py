"""Embeddings protocol — contract for text → vector encoders."""

from __future__ import annotations

from typing import Protocol


class Embeddings(Protocol):
    """Adapter contract — every impl satisfies the same surface so
    callers can swap backends via config without code changes.

    Embeddings are NOT tenant-scoped. The model only sees raw text, no
    tenant context — there's nothing for the impl to isolate. (Contrast
    with Store / FileStorage which require tenant_id on every method.)
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch. Result preserves input order. Empty input
        → empty output. Raises on transient errors so callers can retry
        with backoff."""
        ...

    def embed_one(self, text: str) -> list[float]:
        """Convenience for single-text encoding. Equivalent to
        `embed([text])[0]`. Provided because the FAQ retrieval call site
        always embeds a single query."""
        ...

    @property
    def dimensions(self) -> int:
        """Vector dimension. Lets callers pre-allocate / validate on
        startup that the index matches the live embedder."""
        ...

    @property
    def model(self) -> str:
        """Model identifier (e.g. "text-embedding-3-small"). Used for
        logging + cache invalidation when the model changes."""
        ...
