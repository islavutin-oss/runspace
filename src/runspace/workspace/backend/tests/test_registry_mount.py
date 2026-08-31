"""Mounting many tenants' gateways on one app."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runspace.workspace.backend.gateway import WorkspaceGateway
from runspace.workspace.backend.registry import WorkspaceRegistry


def _gateway(tmp_path, name: str, tenant: str) -> WorkspaceGateway:
    """A gateway built the way a host builds one, so the handlers under test
    see the state they actually expect."""
    d = tmp_path / tenant
    d.mkdir()
    (d / "workspace.yml").write_text(
        f"name: {name}\napps:\n  helper:\n    name: Ada\n    role: Helper\n"
    )
    return WorkspaceGateway.from_config(str(d / "workspace.yml"))


@pytest.fixture
def client(tmp_path):
    reg = WorkspaceRegistry()
    reg.register("acme", _gateway(tmp_path, "Acme", "acme"))
    reg.register("globex", _gateway(tmp_path, "Globex", "globex"))
    app = FastAPI()
    mounted = reg.mount(app)
    assert mounted > 0, "mount() reported no routes"
    return TestClient(app), reg, mounted


def test_mount_exposes_the_whole_gateway_surface_not_a_subset(client):
    _, reg, mounted = client
    template = reg.get("acme")
    from fastapi.routing import APIRoute

    expected = sum(1 for r in template.router.routes if isinstance(r, APIRoute))
    assert mounted == expected, (
        f"mounted {mounted} of {expected} gateway routes — a partial mount is "
        "how hosts lose channels, uploads and pairings without noticing"
    )


def test_the_same_path_answers_for_the_tenant_in_the_host_header(client):
    c, _, _ = client
    acme = c.get("/api/workspace/config", headers={"host": "acme.example.com"})
    globex = c.get("/api/workspace/config", headers={"host": "globex.example.com"})
    assert acme.status_code == 200 and globex.status_code == 200
    assert acme.json()["name"] == "Acme"
    assert globex.json()["name"] == "Globex"


def test_an_unrecognised_host_falls_back_to_the_first_workspace(client):
    """This is `resolve()`'s documented behaviour, and hosts depend on it — an
    apex domain with no matching slug is meant to land on the default tenant.

    It is pinned here because it is also a sharp edge: a Host header nobody
    recognises gets a real workspace rather than a 404. Anyone relying on the
    host header for isolation needs to know that, and anyone changing this
    should have to change a test that says so out loud.
    """
    c, reg, _ = client
    r = c.get("/api/workspace/config", headers={"host": "nobody.example.com"})
    assert r.status_code == 200
    first = next(iter(reg.slugs))
    assert r.json()["name"] == reg.get(first).name


def test_mount_on_an_empty_registry_is_a_no_op(caplog):
    app = FastAPI()
    assert WorkspaceRegistry().mount(app) == 0
