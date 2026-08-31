"""Adapter registry — single config-gated factory for every protocol."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from .config import EmbeddingsConfig, FileStorageConfig, StoreConfig, TransportConfig, VisionConfig
from .embeddings import Embeddings, FixtureEmbeddings, OpenAICompatEmbeddings
from .file_storage import FileStorage, LocalFileStorage
from .store import FileStore, InMemoryStore, Store, SupabaseStore
from .transport import FileInboxTransport, TelegramTransport, Transport
from .vision import CodexVision, FixtureVision, Vision

log = logging.getLogger(__name__)

# ── Back-compat helpers ───────────────────────────────────────────────────


def is_sandbox() -> bool:
    """True when ANY protocol is wired to a non-prod backend.

    Cheap, non-validating — just inspects raw env. Suitable for cosmetic
    decisions (badge in UI, log prefix). New code should ask the
    specific config class it cares about, which validates required env.
    """
    import os

    if os.environ.get("APP_MODE") == "sandbox":
        return True
    if os.environ.get("STORE_BACKEND") in ("file", "memory"):
        return True
    if os.environ.get("VISION_BACKEND") == "fixture":
        return True
    if os.environ.get("TRANSPORT_BACKEND") == "file":
        return True
    if os.environ.get("STORAGE_BACKEND") == "local":
        return True
    if os.environ.get("EMBEDDINGS_BACKEND") == "fixture":
        return True
    return False


# ── Store ─────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_store() -> Store:
    cfg = StoreConfig.from_env()
    if cfg.backend == "supabase":
        url, key = cfg.supabase_credentials()
        return SupabaseStore(url=url, key=key)
    if cfg.backend == "file":
        return FileStore(Path(cfg.file_root))  # type: ignore[arg-type]
    return InMemoryStore()


# ── Vision ────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_vision() -> Vision:
    cfg = VisionConfig.from_env()
    if cfg.backend == "fixture":
        return FixtureVision(Path(cfg.fixtures_dir))  # type: ignore[arg-type]
    return CodexVision()


# ── Transport ─────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_transport() -> Transport:
    cfg = TransportConfig.from_env()
    if cfg.backend == "file":
        return FileInboxTransport(Path(cfg.inbox_dir))  # type: ignore[arg-type]
    return TelegramTransport()


# ── File Storage ──────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_file_storage() -> FileStorage:
    cfg = FileStorageConfig.from_env()
    if cfg.backend == "supabase":
        from .file_storage.supabase import SupabaseFileStorage  # lazy import

        return SupabaseFileStorage(bucket=cfg.supabase_bucket)
    return LocalFileStorage(Path(cfg.local_root))  # type: ignore[arg-type]


# ── Embeddings ────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    cfg = EmbeddingsConfig.from_env()
    if cfg.backend == "openai":
        if OpenAICompatEmbeddings is None:
            # The class is None when the `openai` SDK is absent. Saying so here
            # beats "'NoneType' object is not callable" from the call below.
            raise RuntimeError(
                "EMBEDDINGS_BACKEND=openai needs the `openai` SDK: "
                "pip install 'runspace[embeddings]'. "
                "Use EMBEDDINGS_BACKEND=fixture for offline runs."
            )
        return OpenAICompatEmbeddings(
            base_url=cfg.base_url,  # type: ignore[arg-type]
            api_key=cfg.api_key,  # type: ignore[arg-type]
            model=cfg.model,
            dimensions=cfg.dimensions,
        )
    return FixtureEmbeddings(dimensions=cfg.dimensions, model=cfg.model)


def reset() -> None:
    """Clear factory caches. Call from tests when changing env mid-process
    (otherwise you get the previous config's impl)."""
    log.debug("[protocols.registry] reset() — clearing all factory caches")
    get_store.cache_clear()
    get_vision.cache_clear()
    get_transport.cache_clear()
    get_file_storage.cache_clear()
    get_embeddings.cache_clear()
