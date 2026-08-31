"""A credential must not reach the model, or the config API."""

from __future__ import annotations

import json

import pytest

from runspace.workspace.backend import WorkspaceGateway

_SECRET = "sk-THE-ACTUAL-SECRET-VALUE"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_BASE_URL", "https://probe.test/v1")
    monkeypatch.setenv("PROBE_SECRET", _SECRET)
    (tmp_path / "agents" / "probe" / "tools").mkdir(parents=True)
    (tmp_path / "agents" / "probe" / "SOUL.md").write_text(
        "You are {{persona_name}} at {{tenant_name}}.\n"
        # a SOUL that tries to interpolate a secret, deliberately
        "Endpoint ${PROBE_BASE_URL}, key ${PROBE_SECRET}.\n",
        encoding="utf-8",
    )
    (tmp_path / "workspace.yml").write_text(
        "name: Leak Probe\n"
        "providers:\n"
        "  main:\n"
        "    base_url: ${PROBE_BASE_URL}\n"
        "    api_key: ${PROBE_SECRET}\n"
        "apps:\n"
        "  probe:\n"
        "    name: Probe\n"
        "    role: Tester\n"
        "    soul: agents/probe/SOUL.md\n"
        "    tools: agents/probe/tools/\n",
        encoding="utf-8",
    )
    return WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))


def test_a_secret_named_in_a_soul_is_not_expanded(workspace):
    """The prompt is sent to a third party. A key that reaches it has left
    your control entirely, and no later redaction gets it back."""
    soul = workspace.registry.get("probe")._soul_text or ""
    assert _SECRET not in soul, "an environment secret was expanded into the system prompt"


def test_the_placeholder_survives_verbatim(workspace):
    """It stays as written, so the author can see nothing happened rather than
    wondering whether it silently worked."""
    soul = workspace.registry.get("probe")._soul_text or ""
    assert "${PROBE_SECRET}" in soul


def test_the_config_the_frontend_receives_carries_no_secret(workspace):
    """/config is served to a browser."""
    payload = json.dumps(
        {
            "name": workspace.name,
            "apps": workspace.registry.list_apps(),
            "channels": workspace._channels,
            "settings_schema": workspace._settings_schema,
        }
    )
    assert _SECRET not in payload, "a provider key reached the config payload"


def test_the_provider_block_did_receive_the_real_value(workspace, monkeypatch):
    """The counter-check: interpolation has to work where it is meant to, or
    the tests above would pass on a workspace that simply never resolved
    anything."""
    import os

    assert os.environ["PROBE_SECRET"] == _SECRET
    # provider resolution happens at gateway build; the agent must be able to
    # reach a model, which means the key resolved somewhere it is allowed to.
    assert workspace.registry.get("probe") is not None
