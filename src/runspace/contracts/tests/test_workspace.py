"""Pin the workspace.yml schema. Validates real tenant configs the
runtimes load."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from runspace.contracts import AppConfig, load_workspace


def test_loads_minimal_workspace(tmp_path: Path):
    p = tmp_path / "workspace.yml"
    p.write_text(
        textwrap.dedent("""
        name: Test
        apps:
          ada:
            name: Ada
            type: agentino
            soul: agents/accountant/SOUL.md
    """)
    )
    cfg = load_workspace(p)
    assert cfg.name == "Test"
    assert "ada" in cfg.apps
    assert cfg.apps["ada"].soul == "agents/accountant/SOUL.md"


def test_app_type_validated(tmp_path: Path):
    """Invalid `type:` value is rejected at load time."""
    from pydantic import ValidationError

    p = tmp_path / "workspace.yml"
    p.write_text(
        textwrap.dedent("""
        apps:
          ada:
            type: martian
    """)
    )
    with pytest.raises(ValidationError):
        load_workspace(p)


def test_unknown_top_level_keys_allowed(tmp_path: Path):
    """Tenants legitimately add custom blocks (settings, automation, …) —
    the schema is permissive at the top level. Strict validation lives in
    sub-block schemas (apps, providers, …)."""
    p = tmp_path / "workspace.yml"
    p.write_text(
        textwrap.dedent("""
        name: Test
        settings:
          max_party_size: 8
        custom_block:
          experimental_flag: true
    """)
    )
    cfg = load_workspace(p)
    assert cfg.name == "Test"
    # extra keys preserved on the model
    assert getattr(cfg, "settings", None) is not None or cfg.model_extra is not None


def test_openclaw_app_fields_accepted(tmp_path: Path):
    """The hybrid runtime adds openclaw_plugin / openclaw_skills — must
    parse cleanly."""
    p = tmp_path / "workspace.yml"
    p.write_text(
        textwrap.dedent("""
        apps:
          nova:
            name: Nova
            type: openclaw
            openclaw_plugin: ada-tools
            openclaw_skills:
              - finance_revenue_summary
              - finance_list_invoices
    """)
    )
    cfg = load_workspace(p)
    a = cfg.apps["nova"]
    assert a.type == "openclaw"
    assert a.openclaw_plugin == "ada-tools"
    assert "finance_revenue_summary" in a.openclaw_skills


def test_app_config_defaults():
    """All AppConfig fields have safe defaults — bare `apps: {ada: {}}` parses."""
    a = AppConfig()
    assert a.type == "agentino"
    assert a.enabled is True
    assert a.openclaw_skills == []
    assert a.gates is None
