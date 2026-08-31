"""media._build_transcriber — config → Transcriber wiring."""

from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

from runspace.protocols.transcriber import Transcriber
from runspace.workspace.backend.media import _build_transcriber, _resolve_env_vars


def test_resolve_env_vars_substitutes_set():
    with mock.patch.dict(os.environ, {"FOO": "bar"}):
        assert _resolve_env_vars("${FOO}") == "bar"


def test_resolve_env_vars_uses_default_when_unset():
    # Make sure FOO_MISSING really is unset
    os.environ.pop("FOO_MISSING", None)
    assert _resolve_env_vars("${FOO_MISSING:-fallback}") == "fallback"


def test_resolve_env_vars_passes_non_string_through():
    assert _resolve_env_vars(42) == 42  # type: ignore[arg-type]
    assert _resolve_env_vars(None) is None  # type: ignore[arg-type]


def test_build_transcriber_returns_none_for_no_config():
    assert _build_transcriber(None) is None
    assert _build_transcriber({}) is None


def test_build_transcriber_returns_none_when_disabled():
    cfg = {"enabled": False, "base_url": "x", "api_key": "y"}
    assert _build_transcriber(cfg) is None


def test_build_transcriber_returns_none_without_url_or_key():
    # Empty/missing both → caller didn't actually configure anything
    assert _build_transcriber({"model": "whisper"}) is None
    assert _build_transcriber({"base_url": "", "api_key": ""}) is None


def test_build_transcriber_returns_protocol_satisfying_object():
    """When config is valid AND agentino is importable, the result
    is a Transcriber-shaped object — gateway can `await
    transcriber.transcribe(...)` without inspecting the concrete type."""
    try:
        import agentino.extras.audio  # noqa: F401
    except Exception:
        pytest.skip("agentino.extras.audio not importable in this environment")
    cfg = {"base_url": "https://example/v1", "api_key": "k", "model": "whisper-large-v3"}
    t = _build_transcriber(cfg)
    assert t is not None
    assert isinstance(t, Transcriber)


def test_build_transcriber_returns_none_when_agentino_not_importable():
    """Simulate the IP-hedge case: workspace gateway booting without
    agentino on the import path. The builder must not crash; it
    quietly returns None and the gateway logs 'transcriber=no'."""
    # Replace agentino.extras.audio with a missing module so the
    # `from ... import AudioTranscriber` inside _build_transcriber fails.
    saved = sys.modules.pop("agentino.extras.audio", None)
    sys.modules["agentino.extras.audio"] = None  # forces ImportError on `from`
    try:
        cfg = {"base_url": "https://example/v1", "api_key": "k"}
        assert _build_transcriber(cfg) is None
    finally:
        if saved is not None:
            sys.modules["agentino.extras.audio"] = saved
        else:
            sys.modules.pop("agentino.extras.audio", None)
