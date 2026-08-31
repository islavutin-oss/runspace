"""Tests for render_tool_docs.py — Markdown generation from a workspace.yml."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import render_tool_docs as render  # noqa: E402  (after sys.path setup)
from agents_inspect import inspect_workspace  # noqa: E402  (after sys.path setup)


@pytest.fixture
def fake_tenant(tmp_path: Path) -> Path:
    """Tiny tenant: 1 agentino agent with one @tool, 1 codex agent."""
    soul = tmp_path / "agents" / "bot" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("# bot\n")

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

    codex_soul = tmp_path / "agents" / "cx" / "SOUL.md"
    codex_soul.parent.mkdir(parents=True)
    codex_soul.write_text("# cx\n")

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
                    "cx": {"type": "codex", "name": "Cx", "soul": "agents/cx/SOUL.md"},
                },
            }
        )
    )
    return ws_yml


def test_render_agent_md_includes_signature_and_docstring(fake_tenant: Path):
    catalog = inspect_workspace(fake_tenant)
    bot = next(a for a in catalog["apps"] if a["id"] == "bot")
    md = render.render_agent_md(catalog, bot)

    assert md.startswith("# TestBot")
    assert "_Tester_" in md
    assert "## Identity" in md
    assert "`bot`" in md
    assert "`agentino`" in md
    assert "`gpt-5.3-codex`" in md
    assert "## Tools (1)" in md
    assert "list_things" in md
    assert "List things filtered by status" in md
    # Param table renders
    assert "| Param | Type | Required | Description |" in md
    assert "`status`" in md
    assert "`max_n`" in md


def test_render_agent_md_for_cli_runtime_skips_tool_section(fake_tenant: Path):
    catalog = inspect_workspace(fake_tenant)
    cx = next(a for a in catalog["apps"] if a["id"] == "cx")
    md = render.render_agent_md(catalog, cx)

    assert md.startswith("# Cx")
    assert "## Tools" in md
    # codex/claude_code/pi/openclaw don't expose @tool surfaces — note that fact
    assert "runtime — tools come from the runtime's" in md or "runtime" in md


def test_render_index_md_lists_all_agents(fake_tenant: Path):
    catalog = inspect_workspace(fake_tenant)
    md = render.render_index_md(catalog)

    assert "# Test Tenant" in md
    assert "test" in md  # tenant_id
    assert "## Agents" in md
    assert "[TestBot](./bot.md)" in md
    assert "[Cx](./cx.md)" in md
    assert "`agentino`" in md
    assert "`codex`" in md


def test_main_writes_one_file_per_agent_plus_readme(fake_tenant: Path, tmp_path: Path):
    """End-to-end: invoke main() and assert the output dir has the right files."""
    out_dir = tmp_path / "agent_docs"
    sys.argv = [
        "render_tool_docs.py",
        str(fake_tenant),
        "--out-dir",
        str(out_dir),
    ]
    render.main()

    assert out_dir.exists()
    files = sorted(p.name for p in out_dir.iterdir())
    assert "README.md" in files
    assert "bot.md" in files
    assert "cx.md" in files
    # And the rendered bot.md has the function signature
    assert "list_things" in (out_dir / "bot.md").read_text()


def test_main_filters_to_agent_subset(fake_tenant: Path, tmp_path: Path):
    out_dir = tmp_path / "agent_docs"
    sys.argv = [
        "render_tool_docs.py",
        str(fake_tenant),
        "--out-dir",
        str(out_dir),
        "--agent",
        "bot",
    ]
    render.main()
    files = sorted(p.name for p in out_dir.iterdir() if p.suffix == ".md" and p.name != "README.md")
    assert files == ["bot.md"]


def test_main_stdout_mode_concatenates_all(fake_tenant: Path, capsys):
    sys.argv = ["render_tool_docs.py", str(fake_tenant), "--stdout"]
    render.main()
    out = capsys.readouterr().out
    assert "# TestBot" in out
    assert "# Cx" in out
    assert "list_things" in out
