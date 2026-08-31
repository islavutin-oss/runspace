"""Flattening a workspace's SOULs through the canonical contract."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runspace.protocols.prompt import flatten_soul


def _workspace(root: Path, *, agents: dict[str, dict], partials: dict[str, str]) -> Path:
    """Write a workspace.yml plus the SOULs and partials it references."""
    apps = {}
    for agent_id, spec in agents.items():
        soul_dir = root / "agents" / agent_id
        # Includes resolve relative to the SOUL's own directory, not the
        # workspace root — so the partials live beside it.
        (soul_dir / "partials").mkdir(parents=True, exist_ok=True)
        for name, body in partials.items():
            (soul_dir / "partials" / name).write_text(body, encoding="utf-8")
        (soul_dir / "SOUL.md").write_text(spec.pop("soul_text"), encoding="utf-8")
        apps[agent_id] = {"soul": f"agents/{agent_id}/SOUL.md", **spec}

    (root / "workspace.yml").write_text(
        yaml.safe_dump({"name": "Acme Back Office", "apps": apps}), encoding="utf-8"
    )
    return root


RENDERING_RULES = "## Rendering Rules\n\nEmit fenced blocks, not markdown tables.\n"


@pytest.fixture
def tenant(tmp_path):
    return _workspace(
        tmp_path,
        partials={"rendering.md": RENDERING_RULES},
        agents={
            "analyst": {
                "name": "Ada",
                "group": "backoffice",
                "soul_text": (
                    "You are {{persona_name}} at {{tenant_name}}.\n\n"
                    "{{include:partials/rendering.md}}\n"
                ),
            },
            "greeter": {
                "name": "Robin",
                "group": "customer",
                "soul_text": "You are {{persona_name}}, greeting visitors to {{tenant_name}}.\n",
            },
        },
    )


def _flatten_all(root: Path):
    cfg = yaml.safe_load((root / "workspace.yml").read_text())
    name = cfg["name"].replace(" Back Office", "").strip()
    out = {}
    for agent_id, app in cfg["apps"].items():
        out[agent_id] = (
            app,
            flatten_soul(root / app["soul"], persona_name=app["name"], tenant_name=name),
        )
    return out


def test_every_soul_flattens_to_something(tenant):
    for agent_id, (_, text) in _flatten_all(tenant).items():
        assert text.strip(), f"{agent_id}: flattened SOUL is empty — a stale soul: path"


def test_no_placeholder_survives_flattening(tenant):
    """An unsubstituted `{{persona_name}}` reaches the model verbatim and the
    agent introduces itself with a template."""
    for agent_id, (_, text) in _flatten_all(tenant).items():
        for token in ("{{persona_name}}", "{{tenant_name}}", "{{include:"):
            assert token not in text, f"{agent_id}: {token} survived flattening"


def test_the_substituted_values_are_the_ones_configured(tenant):
    apps = _flatten_all(tenant)
    assert "Ada" in apps["analyst"][1]
    assert "Acme" in apps["analyst"][1]
    assert "Robin" in apps["greeter"][1]


def test_an_include_pulls_its_partial_in(tenant):
    _, text = _flatten_all(tenant)["analyst"]
    assert "Emit fenced blocks" in text, "the included partial did not arrive"


def test_a_missing_partial_warns_and_drops_the_include(tmp_path, caplog):
    """The failure this whole file exists for: a typo'd include path.

    What happens is that flattening logs a warning and drops the include —
    the agent loads with a smaller prompt and answers slightly worse, and the
    only trace is a log line. Pinned deliberately: it is the behaviour, and
    anyone tempted to make it raise should have to change a test that spells
    out what is being traded."""
    root = _workspace(
        tmp_path,
        partials={},
        agents={
            "analyst": {
                "name": "Ada",
                "group": "backoffice",
                "soul_text": "You are {{persona_name}}.\n\n{{include:partials/nope.md}}\n",
            }
        },
    )
    import logging

    with caplog.at_level(logging.WARNING):
        text = flatten_soul(root / "agents/analyst/SOUL.md", persona_name="Ada", tenant_name="Acme")
    assert "{{include:" not in text, "the marker is dropped, not left in the prompt"
    assert any("include not found" in r.message for r in caplog.records), (
        "a missing partial vanished with no warning at all — nothing would tell "
        "an operator the agent had lost instructions"
    )
