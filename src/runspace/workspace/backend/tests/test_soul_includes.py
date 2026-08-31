"""SOUL.md {{include:...}} directive — shared prompt blocks across agents."""

import pytest

from runspace.workspace.backend.app_registry import (  # noqa: E402  (after sys.path setup)
    AgentApp,
    AppRegistry,
)


@pytest.fixture
def tmp_soul_dir(tmp_path):
    """Tenant-like directory with SOUL.md + shared partials."""
    partials = tmp_path / "_partials"
    partials.mkdir()
    (partials / "policies.md").write_text(
        "## Shared Policies\n- Do not add 'Prepared for' signatures\n- Keep reports concise\n"
    )
    (partials / "tools.md").write_text(
        "## Available Document Tools\n- `create_pdf` — branded PDF\n- `create_csv` — exports\n"
    )
    return tmp_path


@pytest.fixture
def registry():
    return AppRegistry(workspace_name="Test")


def _mk_app(soul_path: str, app_id: str = "analytics", name: str = "Luca") -> AgentApp:
    return AgentApp(
        id=app_id,
        name=name,
        role="Analytics Strategist",
        type="agentino",
        soul_path=soul_path,
    )


class TestSoulIncludes:
    def test_simple_include(self, tmp_soul_dir, registry):
        soul = tmp_soul_dir / "SOUL.md"
        soul.write_text("# Luca\n\n{{include:_partials/policies.md}}\n\nMain content here.\n")
        registry.register(_mk_app(str(soul)))
        text = registry.apps["analytics"]._soul_text
        assert "Shared Policies" in text
        assert "Prepared for" in text
        assert "Main content here" in text
        assert "{{include:" not in text, "include directive should be fully expanded"

    def test_multiple_includes(self, tmp_soul_dir, registry):
        soul = tmp_soul_dir / "SOUL.md"
        soul.write_text(
            "# Agent\n{{include:_partials/policies.md}}\n\n{{include:_partials/tools.md}}\n"
        )
        registry.register(_mk_app(str(soul)))
        text = registry.apps["analytics"]._soul_text
        assert "Shared Policies" in text
        assert "Available Document Tools" in text
        assert "create_pdf" in text

    def test_missing_include_renders_empty(self, tmp_soul_dir, registry):
        soul = tmp_soul_dir / "SOUL.md"
        soul.write_text("# Agent\n{{include:_partials/nonexistent.md}}\nRest\n")
        registry.register(_mk_app(str(soul)))
        text = registry.apps["analytics"]._soul_text
        assert "{{include:" not in text
        assert "Rest" in text

    def test_nested_include(self, tmp_soul_dir, registry):
        """Includes can nest: policies.md can itself include tools.md."""
        (tmp_soul_dir / "_partials" / "combined.md").write_text(
            "## Combined\n{{include:policies.md}}\n{{include:tools.md}}\n"
        )
        soul = tmp_soul_dir / "SOUL.md"
        soul.write_text("# Agent\n{{include:_partials/combined.md}}\n")
        registry.register(_mk_app(str(soul)))
        text = registry.apps["analytics"]._soul_text
        assert "Shared Policies" in text
        assert "Available Document Tools" in text

    def test_include_plays_nice_with_template_vars(self, tmp_soul_dir, registry):
        """{{persona_name}} and {{tenant_name}} still substitute after includes."""
        (tmp_soul_dir / "_partials" / "greet.md").write_text(
            "You are {{persona_name}} at {{tenant_name}}."
        )
        soul = tmp_soul_dir / "SOUL.md"
        soul.write_text("# Agent\n{{include:_partials/greet.md}}\n")
        registry.register(_mk_app(str(soul), name="Luca"))
        text = registry.apps["analytics"]._soul_text
        assert "You are Luca at" in text
        assert "{{persona_name}}" not in text

    def test_include_cycle_detection(self, tmp_soul_dir, registry):
        """A pathological nested include won't recurse forever (depth limit)."""
        (tmp_soul_dir / "_partials" / "loop.md").write_text("LOOP\n{{include:loop.md}}\n")
        soul = tmp_soul_dir / "SOUL.md"
        soul.write_text("{{include:_partials/loop.md}}")
        # Must not hang or recurse infinitely
        registry.register(_mk_app(str(soul)))
        text = registry.apps["analytics"]._soul_text
        assert "LOOP" in text
        # After depth limit, further includes stay as-is (we bail early)
        # Test just ensures no infinite loop/crash — passes if we get here.
