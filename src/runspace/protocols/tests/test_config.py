"""Config validation regression tests."""

from __future__ import annotations

import os

import pytest

from runspace.protocols.config import (
    EmbeddingsConfig,
    FileStorageConfig,
    StoreConfig,
    TransportConfig,
    VisionConfig,
)

# ── Store config: live (Supabase) backend ──────────────────────────────


class TestStoreConfigSupabase:
    def test_passes_with_explicit_service_key(self, monkeypatch):
        monkeypatch.setenv("STORE_BACKEND", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_explicit")
        cfg = StoreConfig.from_env()
        assert cfg.backend == "supabase"
        url, key = cfg.supabase_credentials()
        assert url == "https://x.supabase.co"
        assert key == "sb_secret_explicit"

    def test_passes_with_legacy_supabase_key(self, monkeypatch):
        """The original regression — acme + initech historically
        set SUPABASE_KEY for the service-role key, not SUPABASE_SERVICE_KEY.
        Both must work."""
        monkeypatch.setenv("STORE_BACKEND", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        monkeypatch.setenv("SUPABASE_KEY", "sb_secret_legacy")
        cfg = StoreConfig.from_env()
        url, key = cfg.supabase_credentials()
        assert key == "sb_secret_legacy"

    def test_explicit_wins_over_legacy(self, monkeypatch):
        monkeypatch.setenv("STORE_BACKEND", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_explicit")
        monkeypatch.setenv("SUPABASE_KEY", "sb_secret_legacy")
        cfg = StoreConfig.from_env()
        _, key = cfg.supabase_credentials()
        assert key == "sb_secret_explicit"

    def test_fails_loudly_when_url_missing(self, monkeypatch):
        monkeypatch.setenv("STORE_BACKEND", "supabase")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "x")
        with pytest.raises(RuntimeError, match="SUPABASE_URL"):
            StoreConfig.from_env()

    def test_fails_loudly_when_both_keys_missing(self, monkeypatch):
        monkeypatch.setenv("STORE_BACKEND", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_KEY"):
            StoreConfig.from_env()


# ── Store config: file backend ─────────────────────────────────────────


class TestStoreConfigFile:
    def test_no_supabase_env_required(self, monkeypatch, tmp_path):
        """If you choose file backend, you should not need any Supabase
        env to boot. This is the whole point of "configure what you use,
        nothing else"."""
        monkeypatch.setenv("STORE_BACKEND", "file")
        monkeypatch.setenv("STORE_FILE_ROOT", str(tmp_path))
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        cfg = StoreConfig.from_env()  # must not raise
        assert cfg.backend == "file"
        assert cfg.file_root == str(tmp_path)

    def test_legacy_data_dir_fallback(self, monkeypatch, tmp_path):
        """Legacy: many older tests + scripts set DATA_DIR instead of
        STORE_FILE_ROOT. Both must work."""
        monkeypatch.setenv("STORE_BACKEND", "file")
        monkeypatch.delenv("STORE_FILE_ROOT", raising=False)
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        cfg = StoreConfig.from_env()
        assert cfg.file_root == str(tmp_path)

    def test_falls_back_to_a_project_local_root(self, monkeypatch, tmp_path):
        """With nothing configured the store still resolves, so a fresh
        install runs before anyone has set an environment variable."""
        monkeypatch.setenv("STORE_BACKEND", "file")
        monkeypatch.delenv("STORE_FILE_ROOT", raising=False)
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        cfg = StoreConfig.from_env()
        assert cfg.file_root == str(tmp_path / ".runspace" / "store")


# ── Store config: memory backend ───────────────────────────────────────


class TestStoreConfigMemory:
    def test_zero_env_required(self, monkeypatch):
        """Memory backend has no required env — used in fast unit tests."""
        monkeypatch.setenv("STORE_BACKEND", "memory")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("STORE_FILE_ROOT", raising=False)
        cfg = StoreConfig.from_env()
        assert cfg.backend == "memory"


# ── Legacy APP_MODE shortcut still works ───────────────────────────────


class TestLegacyAppMode:
    def test_app_mode_sandbox_flips_to_file(self, monkeypatch, tmp_path):
        """Old code sets APP_MODE=sandbox + DATA_DIR. Must still work
        without setting STORE_BACKEND explicitly."""
        monkeypatch.setenv("APP_MODE", "sandbox")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.delenv("STORE_BACKEND", raising=False)
        cfg = StoreConfig.from_env()
        assert cfg.backend == "file"

    def test_explicit_backend_wins_over_app_mode(self, monkeypatch, tmp_path):
        """If both APP_MODE and STORE_BACKEND are set, explicit wins."""
        monkeypatch.setenv("APP_MODE", "sandbox")
        monkeypatch.setenv("STORE_BACKEND", "memory")
        cfg = StoreConfig.from_env()
        assert cfg.backend == "memory"


# ── Vision + Transport: same shape, smoke-test required env ────────────


class TestVisionConfig:
    def test_defaults_to_fixture_so_no_credentials_are_needed(self, monkeypatch):
        for k in ("VISION_BACKEND", "APP_MODE"):
            monkeypatch.delenv(k, raising=False)
        cfg = VisionConfig.from_env()
        assert cfg.backend == "fixture"

    def test_fixture_needs_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VISION_BACKEND", "fixture")
        monkeypatch.setenv("VISION_FIXTURES_DIR", str(tmp_path))
        cfg = VisionConfig.from_env()
        assert cfg.backend == "fixture"
        assert cfg.fixtures_dir == str(tmp_path)


class TestTransportConfig:
    def test_defaults_to_file_inbox_so_no_bot_token_is_needed(self, monkeypatch):
        for k in ("TRANSPORT_BACKEND", "APP_MODE"):
            monkeypatch.delenv(k, raising=False)
        cfg = TransportConfig.from_env()
        assert cfg.backend == "file"

    def test_file_inbox_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRANSPORT_BACKEND", "file")
        monkeypatch.setenv("TRANSPORT_INBOX_DIR", str(tmp_path))
        cfg = TransportConfig.from_env()
        assert cfg.backend == "file"
        assert cfg.inbox_dir == str(tmp_path)


# ── FileStorage config: auto-detect ─────────────────────────────────────


class TestFileStorageAutoDetect:
    """User rule: 'if Supabase Storage is connected, all tools use it;
    if not, fall back to FileStorage.' These tests pin that behavior."""

    def test_supabase_creds_present_picks_supabase(self, monkeypatch):
        for k in ("STORAGE_BACKEND", "APP_MODE"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "sb_secret_xxx")
        cfg = FileStorageConfig.from_env()
        assert cfg.backend == "supabase"

    def test_no_supabase_creds_falls_back_to_local(self, monkeypatch):
        for k in (
            "STORAGE_BACKEND",
            "APP_MODE",
            "SUPABASE_URL",
            "SUPABASE_KEY",
            "SUPABASE_SERVICE_KEY",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("STORAGE_LOCAL_ROOT", "/tmp/x")
        cfg = FileStorageConfig.from_env()
        assert cfg.backend == "local"

    def test_explicit_local_overrides_supabase_creds(self, monkeypatch):
        """If both Supabase env AND STORAGE_BACKEND=local are set,
        explicit choice wins (e.g. dev-against-prod-DB scenario)."""
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "sb_secret_xxx")
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        monkeypatch.setenv("STORAGE_LOCAL_ROOT", "/tmp/x")
        cfg = FileStorageConfig.from_env()
        assert cfg.backend == "local"

    def test_legacy_app_mode_sandbox_forces_local(self, monkeypatch):
        """APP_MODE=sandbox is the back-compat shortcut. Must beat
        auto-detect even if Supabase env is present."""
        monkeypatch.setenv("APP_MODE", "sandbox")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "sb_secret_xxx")
        monkeypatch.delenv("STORAGE_BACKEND", raising=False)
        monkeypatch.setenv("STORAGE_LOCAL_ROOT", "/tmp/x")
        cfg = FileStorageConfig.from_env()
        assert cfg.backend == "local"

    def test_partial_supabase_env_falls_back_to_local(self, monkeypatch):
        """Half-configured Supabase (URL but no key) is not 'connected' —
        fall back to local rather than failing at boot."""
        for k in ("STORAGE_BACKEND", "APP_MODE", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("STORAGE_LOCAL_ROOT", "/tmp/x")
        cfg = FileStorageConfig.from_env()
        assert cfg.backend == "local"


class TestZeroConfiguration:
    """A fresh install must build every adapter without any environment."""

    def test_every_backend_resolves_with_an_empty_environment(self, monkeypatch, tmp_path):
        prefixes = {
            "STORE",
            "STORAGE",
            "VISION",
            "TRANSPORT",
            "EMBEDDINGS",
            "SUPABASE",
            "AI",
            "APP",
        }
        for key in list(os.environ):
            if key.split("_")[0] in prefixes:
                monkeypatch.delenv(key, raising=False)
        monkeypatch.chdir(tmp_path)
        assert StoreConfig.from_env().backend == "file"
        assert VisionConfig.from_env().backend == "fixture"
        assert TransportConfig.from_env().backend == "file"
        assert EmbeddingsConfig.from_env().backend == "fixture"
