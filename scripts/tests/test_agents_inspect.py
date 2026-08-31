"""Tests for `runspace/scripts/agents_inspect.py`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import agents_inspect as inspect  # noqa: E402  (after sys.path setup)


@pytest.fixture
def fake_tenant(tmp_path: Path) -> Path:
    """Build a tiny tenant tree: workspace.yml + 1 agentino agent + 1 codex agent."""
    soul = tmp_path / "agents" / "bot" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("# Bot persona — {{persona_name}}\n")

    tools_dir = tmp_path / "agents" / "bot" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "list_things.py").write_text(
        "from agentino import tool\n"
        "\n"
        "@tool\n"
        "def list_things(status: str = 'open', max_n: int = 10) -> str:\n"
        '    """List things filtered by status."""\n'
        "    return ''\n"
    )

    soul_codex = tmp_path / "agents" / "codex_bot" / "SOUL.md"
    soul_codex.parent.mkdir(parents=True)
    soul_codex.write_text("# Codex bot\n")

    ws_yml = tmp_path / "workspace.yml"
    ws_yml.write_text(
        yaml.safe_dump(
            {
                "name": "Test Tenant",
                "tenant_id": "test",
                "providers": {"router": {"base_url": "x", "api_key": "k"}},
                "apps": {
                    "bot": {
                        "type": "agentino",
                        "name": "TestBot",
                        "role": "Tester",
                        "soul": "agents/bot/SOUL.md",
                        "tools": "agents/bot/tools",
                        "model": "gpt-5.3-codex",
                    },
                    "codex_bot": {
                        "type": "codex",
                        "name": "CodexBot",
                        "soul": "agents/codex_bot/SOUL.md",
                    },
                    "disabled_one": {
                        "type": "agentino",
                        "enabled": False,
                        "soul": "agents/bot/SOUL.md",
                    },
                },
            }
        )
    )
    return ws_yml


def test_inspect_workspace_returns_catalog_shape(fake_tenant: Path):
    catalog = inspect.inspect_workspace(fake_tenant)
    assert catalog["tenant"] == "Test Tenant"
    assert catalog["tenant_id"] == "test"
    assert "router" in catalog["providers"]
    # Disabled apps excluded; enabled apps included
    ids = [a["id"] for a in catalog["apps"]]
    assert "bot" in ids
    assert "codex_bot" in ids
    assert "disabled_one" not in ids


def test_inspect_app_extracts_typed_tool_signature(fake_tenant: Path):
    catalog = inspect.inspect_workspace(fake_tenant)
    bot = next(a for a in catalog["apps"] if a["id"] == "bot")
    assert bot["type"] == "agentino"
    assert bot["model"] == "gpt-5.3-codex"
    assert bot["tools"], "expected at least one tool extracted from tools_dir"

    list_things = next(t for t in bot["tools"] if t["name"] == "list_things")
    assert list_things["description"]
    assert "List things filtered by status" in list_things["description"]
    param_names = {p["name"] for p in list_things["params"]}
    assert "status" in param_names
    assert "max_n" in param_names


def test_inspect_app_for_non_agentino_returns_empty_tools(fake_tenant: Path):
    """codex/claude_code/openclaw/pi runtimes have built-in tools, not @tool dirs."""
    catalog = inspect.inspect_workspace(fake_tenant)
    cx = next(a for a in catalog["apps"] if a["id"] == "codex_bot")
    assert cx["type"] == "codex"
    assert cx["tools"] == []  # no Python @tool extraction for CLI runtimes


def test_inspect_workspace_filter_to_subset(fake_tenant: Path):
    catalog = inspect.inspect_workspace(fake_tenant, agent_filter=["bot"])
    assert [a["id"] for a in catalog["apps"]] == ["bot"]


def test_inspect_workspace_handles_missing_tools_dir(tmp_path: Path):
    """If tools: points at a non-existent path, return empty tools (don't raise)."""
    soul = tmp_path / "agents" / "bot" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("# bot\n")

    ws_yml = tmp_path / "workspace.yml"
    ws_yml.write_text(
        yaml.safe_dump(
            {
                "name": "T",
                "apps": {
                    "bot": {
                        "type": "agentino",
                        "soul": "agents/bot/SOUL.md",
                        "tools": "agents/bot/nonexistent_tools_dir",
                    }
                },
            }
        )
    )
    catalog = inspect.inspect_workspace(ws_yml)
    bot = catalog["apps"][0]
    assert bot["tools"] == []
    assert "tools_error" not in bot  # cleanly empty, not an error


def test_render_text_emits_human_readable_output(fake_tenant: Path):
    catalog = inspect.inspect_workspace(fake_tenant)
    rendered = inspect.render_text(catalog)
    assert "Test Tenant" in rendered
    assert "bot" in rendered
    assert "TestBot" in rendered
    assert "list_things" in rendered
    # Codex bot is rendered too
    assert "codex_bot" in rendered
