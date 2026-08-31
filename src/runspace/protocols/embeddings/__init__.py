"""Embeddings — text → vector adapter."""

from __future__ import annotations

from .fixture import FixtureEmbeddings
from .protocol import Embeddings

# OpenAICompatEmbeddings needs the `openai` SDK. Lazy so tests can run
# with FixtureEmbeddings without pulling the prod backend lib.
try:
    from .openai_compat import OpenAICompatEmbeddings
except ImportError:  # pragma: no cover
    OpenAICompatEmbeddings = None  # type: ignore[assignment]

__all__ = ["Embeddings", "FixtureEmbeddings", "OpenAICompatEmbeddings"]
