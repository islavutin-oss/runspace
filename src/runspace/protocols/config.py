"""Single source of truth for adapter configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── App-level mode (back-compat shortcut) ─────────────────────────────────


def _legacy_app_mode() -> str:
    """The original APP_MODE switch. Still honored as a shortcut for
    deploys that only set one variable. New deploys should use the
    per-protocol BACKEND vars below."""
    return os.environ.get("APP_MODE", "live")


def _legacy_is_sandbox() -> bool:
    return _legacy_app_mode() == "sandbox"


# ── Store config ──────────────────────────────────────────────────────────


class StoreConfig(BaseSettings):
    """Which Store backend to wire + the env it needs.

    Env vars (case-insensitive, prefixed with STORE_):
      STORE_BACKEND    — supabase | file | memory
      STORE_FILE_ROOT  — required when backend=file (or DATA_DIR fallback)

    The Supabase fields are read from the unprefixed env so they share
    the conventional Supabase environment names
    (SUPABASE_URL / SUPABASE_KEY).
    """

    model_config = SettingsConfigDict(
        env_prefix="STORE_",
        case_sensitive=False,
        extra="ignore",
    )

    backend: Literal["supabase", "file", "memory"] = "file"
    file_root: str | None = None  # STORE_FILE_ROOT or fallback to DATA_DIR

    @classmethod
    def from_env(cls) -> StoreConfig:
        """Build with legacy fallbacks resolved before validation."""
        # Legacy shortcut: APP_MODE=sandbox flips backend to file.
        backend = os.environ.get("STORE_BACKEND")
        if not backend and _legacy_is_sandbox():
            backend = "file"
        backend = backend or "file"

        # STORE_FILE_ROOT, else DATA_DIR, else a project-local directory so
        # an unconfigured install still runs.
        file_root = (
            os.environ.get("STORE_FILE_ROOT")
            or os.environ.get("DATA_DIR")
            or str(Path.cwd() / ".runspace" / "store")
        )

        return cls(backend=backend, file_root=file_root)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _check(self) -> StoreConfig:
        if self.backend == "supabase":
            url = os.environ.get("SUPABASE_URL")
            key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
            if not url or not key:
                raise RuntimeError(
                    "STORE_BACKEND=supabase requires SUPABASE_URL and "
                    "SUPABASE_SERVICE_KEY (or SUPABASE_KEY) in env. "
                    "Set them, or switch to STORE_BACKEND=file for "
                    "Supabase-free deploys."
                )
        elif self.backend == "file":
            if not self.file_root:
                raise RuntimeError(
                    "STORE_BACKEND=file requires STORE_FILE_ROOT (or DATA_DIR) "
                    "pointing at a writable directory."
                )
        # memory backend has no required env
        return self

    def supabase_credentials(self) -> tuple[str, str]:
        """Return (url, key). Only call when backend == 'supabase'."""
        url = os.environ["SUPABASE_URL"]
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
        return url, key


# ── Vision config ─────────────────────────────────────────────────────────


class VisionConfig(BaseSettings):
    """Which Vision backend to wire.

    Env vars:
      VISION_BACKEND       — codex | fixture
      VISION_FIXTURES_DIR  — required when backend=fixture
    """

    model_config = SettingsConfigDict(
        env_prefix="VISION_",
        case_sensitive=False,
        extra="ignore",
    )

    backend: Literal["codex", "fixture"] = "fixture"
    fixtures_dir: str | None = None

    @classmethod
    def from_env(cls) -> VisionConfig:
        backend = os.environ.get("VISION_BACKEND")
        if not backend and _legacy_is_sandbox():
            backend = "fixture"
        backend = backend or "fixture"
        fixtures_dir = os.environ.get("VISION_FIXTURES_DIR") or "tests/fixtures/vision"
        return cls(backend=backend, fixtures_dir=fixtures_dir)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _check(self) -> VisionConfig:
        if self.backend == "fixture" and not self.fixtures_dir:
            raise RuntimeError("VISION_BACKEND=fixture requires VISION_FIXTURES_DIR")
        return self


# ── File storage config ───────────────────────────────────────────────────


class FileStorageConfig(BaseSettings):
    """Which FileStorage backend to wire.

    Env vars:
      STORAGE_BACKEND     — local | supabase
      STORAGE_LOCAL_ROOT  — required when backend=local (or
                            FILE_STORAGE_ROOT / WORKSPACE_FILES_DIR)
      STORAGE_SUPABASE_BUCKET — required when backend=supabase
                                (default: 'workspace-files')
    """

    model_config = SettingsConfigDict(
        env_prefix="STORAGE_",
        case_sensitive=False,
        extra="ignore",
    )

    backend: Literal["local", "supabase"] = "local"
    local_root: str | None = None
    supabase_bucket: str = "workspace-files"

    @classmethod
    def from_env(cls) -> FileStorageConfig:
        """Resolve backend with auto-detect.

        Precedence:
          1. Explicit STORAGE_BACKEND env var (always wins)
          2. APP_MODE=sandbox legacy shortcut → local
          3. Auto-detect: Supabase creds present → supabase; else local

        Rationale: 'if Supabase Storage is connected, all tools use it;
        if not, fall back to FileStorage'. Same physical key (the
        service-role key) gates both Store and FileStorage — there's no
        scenario where Store is on Supabase but FileStorage isn't.
        """
        explicit = os.environ.get("STORAGE_BACKEND")
        if explicit:
            backend = explicit
        elif _legacy_is_sandbox():
            backend = "local"
        else:
            url = os.environ.get("SUPABASE_URL")
            key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
            backend = "supabase" if (url and key) else "local"

        local_root = (
            os.environ.get("STORAGE_LOCAL_ROOT")
            or os.environ.get("FILE_STORAGE_ROOT")
            or os.environ.get("WORKSPACE_FILES_DIR")
            or "/tmp/workspace-files"
        )
        bucket = os.environ.get("STORAGE_SUPABASE_BUCKET") or "workspace-files"
        return cls(
            backend=backend,
            local_root=local_root,  # type: ignore[arg-type]
            supabase_bucket=bucket,
        )

    @model_validator(mode="after")
    def _check(self) -> FileStorageConfig:
        if self.backend == "local":
            if not self.local_root:
                raise RuntimeError(
                    "STORAGE_BACKEND=local requires STORAGE_LOCAL_ROOT (or "
                    "FILE_STORAGE_ROOT / WORKSPACE_FILES_DIR)"
                )
        elif self.backend == "supabase":
            url = os.environ.get("SUPABASE_URL")
            key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
            if not url or not key:
                raise RuntimeError(
                    "STORAGE_BACKEND=supabase requires SUPABASE_URL and "
                    "SUPABASE_SERVICE_KEY (or SUPABASE_KEY) in env."
                )
        return self


# ── Transport config ──────────────────────────────────────────────────────


class TransportConfig(BaseSettings):
    """Which Transport backend to wire.

    Env vars:
      TRANSPORT_BACKEND    — telegram | file
      TRANSPORT_INBOX_DIR  — required when backend=file
    """

    model_config = SettingsConfigDict(
        env_prefix="TRANSPORT_",
        case_sensitive=False,
        extra="ignore",
    )

    backend: Literal["telegram", "file"] = "file"
    inbox_dir: str | None = None

    @classmethod
    def from_env(cls) -> TransportConfig:
        backend = os.environ.get("TRANSPORT_BACKEND")
        if not backend and _legacy_is_sandbox():
            backend = "file"
        backend = backend or "file"
        inbox_dir = (
            os.environ.get("TRANSPORT_INBOX_DIR") or os.environ.get("INBOX_DIR") or "data/inbox"
        )
        return cls(backend=backend, inbox_dir=inbox_dir)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _check(self) -> TransportConfig:
        if self.backend == "file" and not self.inbox_dir:
            raise RuntimeError("TRANSPORT_BACKEND=file requires TRANSPORT_INBOX_DIR")
        return self


# ── Embeddings config ─────────────────────────────────────────────────────


class EmbeddingsConfig(BaseSettings):
    """Which Embeddings backend to wire + the env it needs.

    Env vars (case-insensitive, prefixed with EMBEDDINGS_):
      EMBEDDINGS_BACKEND     — openai | fixture
      EMBEDDINGS_BASE_URL    — required when backend=openai
      EMBEDDINGS_API_KEY     — required when backend=openai
      EMBEDDINGS_MODEL       — model id (default: text-embedding-3-small)
      EMBEDDINGS_DIMENSIONS  — output dim (default: 1536 for OpenAI,
                                32 for fixture)

    If EMBEDDINGS_BASE_URL / EMBEDDINGS_API_KEY are absent, the generic
    AI_BASE_URL / AI_API_KEY pair is used instead.
    """

    model_config = SettingsConfigDict(
        env_prefix="EMBEDDINGS_",
        case_sensitive=False,
        extra="ignore",
    )

    backend: Literal["openai", "fixture"] = "fixture"
    base_url: str | None = None
    api_key: str | None = None
    model: str = "text-embedding-3-small"
    dimensions: int = 1536  # text-embedding-3-small default

    @classmethod
    def from_env(cls) -> EmbeddingsConfig:
        """Resolve backend with auto-detect.

        Precedence:
          1. Explicit EMBEDDINGS_BACKEND env var (always wins)
          2. APP_MODE=sandbox legacy shortcut → fixture
          3. Auto-detect: a base_url + key resolved from any of the
             aliased env vars → openai; else fixture.
        """
        base_url = os.environ.get("EMBEDDINGS_BASE_URL") or os.environ.get("AI_BASE_URL")
        api_key = os.environ.get("EMBEDDINGS_API_KEY") or os.environ.get("AI_API_KEY")
        explicit = os.environ.get("EMBEDDINGS_BACKEND")
        if explicit:
            backend = explicit
        elif _legacy_is_sandbox():
            backend = "fixture"
        else:
            backend = "openai" if (base_url and api_key) else "fixture"

        model = os.environ.get("EMBEDDINGS_MODEL") or "text-embedding-3-small"
        # Default dim depends on backend; fixture is much smaller because
        # tests don't need 1536 dims to exercise cosine math.
        default_dim = 32 if backend == "fixture" else 1536
        dim_str = os.environ.get("EMBEDDINGS_DIMENSIONS")
        dimensions = int(dim_str) if dim_str else default_dim

        return cls(  # type: ignore[arg-type]
            backend=backend,
            base_url=base_url,
            api_key=api_key,
            model=model,
            dimensions=dimensions,
        )

    @model_validator(mode="after")
    def _check(self) -> EmbeddingsConfig:
        if self.backend == "openai":
            if not self.base_url or not self.api_key:
                raise RuntimeError(
                    "EMBEDDINGS_BACKEND=openai requires EMBEDDINGS_BASE_URL "
                    "and EMBEDDINGS_API_KEY (or AI_BASE_URL / AI_API_KEY)."
                )
        if self.dimensions <= 0:
            raise RuntimeError("EMBEDDINGS_DIMENSIONS must be > 0")
        return self
