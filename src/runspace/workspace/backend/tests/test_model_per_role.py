"""A shared demo seat and the owner are not worth the same spend.

The model used to come from `app.model` alone, so every turn cost the same
regardless of who asked — and on a public demo the bulk of the traffic is the
demo seat asking easy questions. `models:` maps a caller role to a model; the
host says which role is calling, and runspace never learns what an account is.
"""

from __future__ import annotations

from runspace.workspace.backend.app_registry import AgentApp
from runspace.workspace.backend.runtimes.claude_code import caller_role


def _app(**kw) -> AgentApp:
    return AgentApp(id="a", name="A", type="claude_code", **kw)


def test_no_mapping_means_the_default_model():
    app = _app(model="opus")
    assert app.model_for(None) == "opus"
    assert app.model_for("demo") == "opus"


def test_a_mapped_role_gets_its_own_model():
    app = _app(model="opus", models={"demo": "sonnet"})
    assert app.model_for("demo") == "sonnet"


def test_an_unmapped_role_falls_back_rather_than_failing():
    """A role nobody listed must not end up with no model at all — that would
    hand the CLI an empty --model and fail the turn."""
    app = _app(model="opus", models={"demo": "sonnet"})
    assert app.model_for("staff") == "opus"
    assert app.model_for(None) == "opus"


def test_the_owner_keeps_the_frontier_model():
    app = _app(model="opus", models={"demo": "sonnet", "owner": "opus"})
    assert app.model_for("owner") == "opus"
    assert app.model_for("demo") == "sonnet"


def test_the_context_var_defaults_to_no_role():
    """Unset means "no opinion" — every existing deployment keeps its model."""
    assert caller_role.get() is None


def test_the_context_var_round_trips():
    token = caller_role.set("demo")
    try:
        assert caller_role.get() == "demo"
    finally:
        caller_role.reset(token)
    assert caller_role.get() is None


def test_config_parsing_ignores_a_malformed_models_value(tmp_path):
    """A list where a mapping belongs must not take the app down."""
    import yaml

    from runspace.workspace.backend.gateway import WorkspaceGateway

    cfg = tmp_path / "workspace.yml"
    cfg.write_text(
        yaml.safe_dump(
            {"name": "T", "apps": {"a": {"name": "A", "soul": "SOUL.md", "models": ["x"]}}}
        )
    )
    (tmp_path / "SOUL.md").write_text("hi")
    gw = WorkspaceGateway.from_config(str(cfg))
    assert gw.registry.get("a").models == {}


def test_config_parsing_reads_the_mapping(tmp_path):
    import yaml

    from runspace.workspace.backend.gateway import WorkspaceGateway

    cfg = tmp_path / "workspace.yml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "name": "T",
                "apps": {
                    "a": {
                        "name": "A",
                        "soul": "SOUL.md",
                        "model": "opus",
                        "models": {"demo": "sonnet", "blank": ""},
                    }
                },
            }
        )
    )
    (tmp_path / "SOUL.md").write_text("hi")
    gw = WorkspaceGateway.from_config(str(cfg))
    app = gw.registry.get("a")
    assert app.models == {"demo": "sonnet"}  # an empty value is no mapping
    assert app.model_for("demo") == "sonnet"
    assert app.model_for("blank") == "opus"
