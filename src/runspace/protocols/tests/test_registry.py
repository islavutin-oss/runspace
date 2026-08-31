"""Tests for the registry — APP_MODE switches the wired impls cleanly."""

from __future__ import annotations

import pytest

from runspace import protocols as services
from runspace.protocols.store import FileStore
from runspace.protocols.transport import FileInboxTransport
from runspace.protocols.vision import FixtureVision


@pytest.fixture(autouse=True)
def _reset_around(monkeypatch):
    """Clear registry caches before AND after each test — APP_MODE
    flips mid-process otherwise stick to whichever impl was first
    requested."""
    services.reset()
    yield
    services.reset()


def test_sandbox_mode_wires_file_store(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_MODE", "sandbox")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = services.get_store()
    assert isinstance(s, FileStore)


def test_sandbox_mode_wires_fixture_vision(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_MODE", "sandbox")
    monkeypatch.setenv("VISION_FIXTURES_DIR", str(tmp_path))
    v = services.get_vision()
    assert isinstance(v, FixtureVision)


def test_sandbox_mode_wires_file_inbox_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_MODE", "sandbox")
    monkeypatch.setenv("INBOX_DIR", str(tmp_path))
    t = services.get_transport()
    assert isinstance(t, FileInboxTransport)


def test_is_sandbox_truthful(monkeypatch):
    monkeypatch.setenv("APP_MODE", "sandbox")
    assert services.is_sandbox() is True
    monkeypatch.setenv("APP_MODE", "live")
    services.reset()
    assert services.is_sandbox() is False


def test_default_mode_is_live(monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)
    assert services.is_sandbox() is False


def test_get_store_caches_within_mode(tmp_path, monkeypatch):
    """Same APP_MODE → same instance (matters for FileStore locks)."""
    monkeypatch.setenv("APP_MODE", "sandbox")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    a = services.get_store()
    b = services.get_store()
    assert a is b


def test_reset_breaks_cache(tmp_path, monkeypatch):
    """After reset(), a new instance is constructed (lets tests flip mode)."""
    monkeypatch.setenv("APP_MODE", "sandbox")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    a = services.get_store()
    services.reset()
    b = services.get_store()
    assert a is not b
