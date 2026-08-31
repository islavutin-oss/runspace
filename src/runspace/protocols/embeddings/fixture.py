"""Deterministic in-memory Embeddings for tests + sandbox."""

from __future__ import annotations

import hashlib
import math


class FixtureEmbeddings:
    """Hash-seeded deterministic embeddings.

    Algorithm: SHA-256 the text, then expand the 32 hash bytes into
    `dimensions` floats by repeating + modulating. Resulting vectors are
    L2-normalized so cosine == dot product, which makes downstream
    similarity math behave sensibly.
    """

    def __init__(self, *, dimensions: int = 32, model: str = "fixture"):
        self._dim = dimensions
        self._model = model

    @property
    def dimensions(self) -> int:
        return self._dim

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        return self._embed_one(text)

    # ── internals ────────────────────────────────────────────────────

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Expand digest bytes to `dimensions` floats. Each float is a
        # byte interpreted as signed [-128, 127] then divided by 128 to
        # land in [-1, 1). This gives plausible variance without
        # collapsing similar texts to identical vectors.
        vec: list[float] = []
        i = 0
        while len(vec) < self._dim:
            b = digest[i % len(digest)]
            signed = b - 128
            vec.append(signed / 128.0)
            i += 1
        # L2 normalize so cosine similarity == dot product downstream.
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec
