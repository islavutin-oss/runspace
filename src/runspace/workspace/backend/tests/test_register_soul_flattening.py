"""Tests for AppRegistry.register()'s SOUL-flattening behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from runspace.workspace.backend.app_registry import AgentApp, AppRegistry

SOUL_BODY = """---
name: bot
type: persona
description: Test persona
---

# Bot — TestRole

You are {{persona_name}} working for {{tenant_name}}. Reply briefly.
"""


@pytest.fixture
def soul_file(tmp_path: Path) -> Path:
    soul = tmp_path / "SOUL.md"
    soul.write_text(SOUL_BODY)
    return soul


def _app(soul_path: Path | str | None, app_type: str = "agentino", app_id: str = "bot") -> AgentApp:
    return AgentApp(
        id=app_id,
        name="TestBot",
        type=app_type,
        soul_path=str(soul_path) if soul_path else None,
    )


# ---------------------------------------------------------------------------
# Pre-existing fix: hard-fail on missing or empty SOUL
# ---------------------------------------------------------------------------


def test_register_raises_filenotfounderror_when_soul_missing():
    """The Ada-permission-loop fix: missing soul_path must HALT registration."""
    registry = AppRegistry(workspace_name="Test")
    app = _app("/no/such/path/SOUL.md")
    with pytest.raises(FileNotFoundError, match="SOUL.md not found"):
        registry.register(app)
    # And the broken app must NOT have been registered
    assert "bot" not in registry.apps


def test_register_raises_valueerror_when_soul_flattens_to_empty(tmp_path: Path):
    """If SOUL exists but resolves empty after include/template chain — halt."""
    empty_soul = tmp_path / "empty.md"
    empty_soul.write_text("")
    registry = AppRegistry(workspace_name="Test")
    app = _app(empty_soul)
    with pytest.raises(ValueError, match="empty"):
        registry.register(app)
    assert "bot" not in registry.apps


def test_register_succeeds_when_soul_is_well_formed(soul_file: Path):
    registry = AppRegistry(workspace_name="Test Tenant")
    app = _app(soul_file)
    registry.register(app)
    assert "bot" in registry.apps
    assert registry.apps["bot"]._soul_text != ""
    # Template tokens were resolved
    assert "TestBot" in registry.apps["bot"]._soul_text


# ---------------------------------------------------------------------------
# Runtime-agnostic flattening fix (commit 224ef6f)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runtime_type", ["agentino", "openclaw", "codex", "claude_code", "pi"])
def test_register_flattens_soul_for_every_runtime_type(soul_file: Path, runtime_type: str):
    """Every runtime that consumes a persona prompt must see the same flattened
    `_soul_text`. Before the fix, only `type=agentino` got SOUL flattening —
    CLI runtimes ran with empty persona."""
    registry = AppRegistry(workspace_name="Test Tenant")
    app = _app(soul_file, app_type=runtime_type, app_id=f"bot-{runtime_type}")
    registry.register(app)
    registered = registry.apps[f"bot-{runtime_type}"]
    assert registered._soul_text, (
        f"_soul_text empty for type={runtime_type!r} — SOUL flattening regressed; "
        f"non-agentino runtimes would run without persona."
    )
    # Same content as agentino — the contract is identical regardless of harness
    assert "TestBot" in registered._soul_text


def test_register_skips_flatten_when_soul_path_absent(tmp_path: Path):
    """An app with no soul_path (e.g. http/webhook) must register cleanly."""
    registry = AppRegistry(workspace_name="Test")
    app = AgentApp(id="http-bot", name="HttpBot", type="http", endpoint="https://x.com/agent")
    registry.register(app)  # must not raise even though soul_path is None
    assert "http-bot" in registry.apps
    assert registry.apps["http-bot"]._soul_text == ""


def test_register_persona_token_uses_app_name(soul_file: Path):
    """`{{persona_name}}` resolves to app.name; `{{tenant_name}}` resolves to
    workspace_name minus ' Back Office' suffix."""
    registry = AppRegistry(workspace_name="Acme Corp Back Office")
    app = _app(soul_file)
    app.name = "Aurora"
    registry.register(app)
    text = registry.apps["bot"]._soul_text
    assert "Aurora" in text
    assert "Acme Corp" in text  # ' Back Office' stripped
    assert "Back Office" not in text  # but not the suffix


def test_register_logs_app_type_for_diagnostic_signal(soul_file: Path, caplog):
    """register() logs the app type so multi-runtime tenants can grep their logs."""
    import logging

    caplog.set_level(logging.INFO)
    registry = AppRegistry(workspace_name="Test")
    app = _app(soul_file, app_type="codex")
    registry.register(app)
    msg = " ".join(rec.message for rec in caplog.records)
    assert "codex" in msg or "bot" in msg
