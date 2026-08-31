"""Tests for runspace.workspace.bootstrap.create_app — the high-level FastAPI bootstrap that replaces hand-rolled tenant lifespan code."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

MINIMAL_WORKSPACE_YML = """
name: Test Tenant
icon: T
brand_color: '#000000'
sidebar_color: '#111111'

providers:
  router:
    base_url: https://router.example.com/v1
    api_key: dummy

users:
  test:
    name: Test User
    role: Owner
    avatar: T
    default: true

channels:
- id: general
  label: general
  icon: Hash
  type: chat
"""


@pytest.fixture
def tenants_dir(tmp_path: Path) -> Path:
    """Build a one-tenant tenants/ directory with a minimal workspace.yml."""
    tenant = tmp_path / "tenants" / "test-tenant"
    tenant.mkdir(parents=True)
    (tenant / "workspace.yml").write_text(MINIMAL_WORKSPACE_YML)
    return tmp_path / "tenants"


def _yml(tenants_dir: Path) -> Path:
    return tenants_dir / "test-tenant" / "workspace.yml"


def test_create_app_returns_fastapi_with_state(tenants_dir):
    """Bootstrap loads workspace.yml and stashes runspace state on app.state."""
    from runspace.workspace import create_app

    app = create_app(
        workspace_yml=_yml(tenants_dir),
        tenant_id="test-tenant",
        enable_cron=False,  # services.cron not on path in this test repo
        enable_telegram_polling=False,
        enable_hybrid_runtime=False,
    )

    assert isinstance(app, FastAPI)
    assert app.state.tenant_id == "test-tenant"
    assert app.state.workspace_gw is not None
    assert app.state.ws_registry is not None


def test_create_app_mounts_extra_routers(tenants_dir):
    """extra_routers passed in are mounted on the returned app."""
    from runspace.workspace import create_app

    extra = APIRouter()

    @extra.get("/_test/ping")
    async def _ping():
        return {"pong": True}

    app = create_app(
        workspace_yml=_yml(tenants_dir),
        tenant_id="test-tenant",
        extra_routers=[extra],
        enable_cron=False,
        enable_telegram_polling=False,
        enable_hybrid_runtime=False,
    )

    with TestClient(app) as client:
        resp = client.get("/_test/ping")
        assert resp.status_code == 200
        assert resp.json() == {"pong": True}


def test_create_app_runs_startup_hooks(tenants_dir):
    """extra_startup_hooks fire during lifespan, see app.state."""
    from runspace.workspace import create_app

    seen_state = {}

    async def my_startup(app):
        seen_state["tenant_id"] = app.state.tenant_id
        seen_state["has_gw"] = app.state.workspace_gw is not None

    app = create_app(
        workspace_yml=_yml(tenants_dir),
        tenant_id="test-tenant",
        extra_startup_hooks=[my_startup],
        enable_cron=False,
        enable_telegram_polling=False,
        enable_hybrid_runtime=False,
    )

    with TestClient(app):
        pass  # entering + exiting context triggers lifespan

    assert seen_state == {"tenant_id": "test-tenant", "has_gw": True}


def test_create_app_runs_shutdown_hooks(tenants_dir):
    """extra_shutdown_hooks fire when lifespan exits."""
    from runspace.workspace import create_app

    shutdown_called = []

    async def on_shutdown(app):
        shutdown_called.append(app.state.tenant_id)

    app = create_app(
        workspace_yml=_yml(tenants_dir),
        tenant_id="test-tenant",
        extra_shutdown_hooks=[on_shutdown],
        enable_cron=False,
        enable_telegram_polling=False,
        enable_hybrid_runtime=False,
    )

    with TestClient(app):
        pass

    assert shutdown_called == ["test-tenant"]


def test_create_app_unknown_tenant_raises(tenants_dir):
    """Missing tenant in registry surfaces a clear error."""
    from runspace.workspace import create_app

    with pytest.raises(RuntimeError, match="not found in registry"):
        create_app(
            workspace_yml=_yml(tenants_dir),
            tenant_id="nonexistent-tenant",
            enable_cron=False,
            enable_telegram_polling=False,
            enable_hybrid_runtime=False,
        )


def test_create_app_missing_yml_raises(tmp_path):
    """workspace.yml that doesn't exist is caught early."""
    from runspace.workspace import create_app

    with pytest.raises(FileNotFoundError):
        create_app(
            workspace_yml=tmp_path / "nope.yml",
            tenant_id="x",
            enable_cron=False,
            enable_telegram_polling=False,
            enable_hybrid_runtime=False,
        )


def test_create_app_resolves_tenant_from_env(tenants_dir, monkeypatch):
    """When tenant_id is None and TENANT_ID env is set, env wins."""
    from runspace.workspace import create_app

    monkeypatch.setenv("TENANT_ID", "test-tenant")

    app = create_app(
        workspace_yml=_yml(tenants_dir),
        tenant_id=None,
        enable_cron=False,
        enable_telegram_polling=False,
        enable_hybrid_runtime=False,
    )
    assert app.state.tenant_id == "test-tenant"


# ─────────────────────────────────────────────────────────────────
# Plugin discovery — the zero-code path. workspace.yml#plugins lists


def _make_yml_with_plugins(tenants_dir: Path, plugin_modules: list[str]) -> Path:
    """Rewrite the test tenant's workspace.yml to include a plugins: list."""
    yml = _yml(tenants_dir)
    plugins_block = "\nplugins:\n" + "".join(f"  - {m}\n" for m in plugin_modules)
    yml.write_text(MINIMAL_WORKSPACE_YML + plugins_block)
    return yml


def test_create_app_discovers_plugin_router(tenants_dir, monkeypatch, tmp_path):
    """A plugin module's `router` is mounted automatically."""
    from runspace.workspace import create_app

    # Build a temporary plugin package in tmp_path/_plugins/
    pkg = tmp_path / "_plugins"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "myplug.py").write_text(
        textwrap.dedent("""
        from fastapi import APIRouter
        router = APIRouter()
        @router.get("/_plugin/hello")
        async def hello():
            return {"from": "plugin"}
    """).lstrip()
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    _make_yml_with_plugins(tenants_dir, ["_plugins.myplug"])

    app = create_app(
        workspace_yml=_yml(tenants_dir),
        tenant_id="test-tenant",
        enable_cron=False,
        enable_telegram_polling=False,
        enable_hybrid_runtime=False,
    )

    with TestClient(app) as client:
        resp = client.get("/_plugin/hello")
        assert resp.status_code == 200
        assert resp.json() == {"from": "plugin"}


def test_create_app_discovers_plugin_startup_hook(tenants_dir, monkeypatch, tmp_path):
    """Plugin's `startup_hooks` list runs during lifespan."""
    from runspace.workspace import create_app

    sentinel_file = tmp_path / "_started"
    pkg = tmp_path / "_plugins2"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "myplug2.py").write_text(
        textwrap.dedent(f"""
        async def _on_startup(app):
            with open({str(sentinel_file)!r}, "w") as f:
                f.write(app.state.tenant_id)
        startup_hooks = [_on_startup]
    """).lstrip()
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    _make_yml_with_plugins(tenants_dir, ["_plugins2.myplug2"])

    app = create_app(
        workspace_yml=_yml(tenants_dir),
        tenant_id="test-tenant",
        enable_cron=False,
        enable_telegram_polling=False,
        enable_hybrid_runtime=False,
    )

    with TestClient(app):
        pass

    assert sentinel_file.read_text() == "test-tenant"


def test_create_app_failed_plugin_raises(tenants_dir):
    """Plugin module that fails to import surfaces the error loudly."""
    from runspace.workspace import create_app

    _make_yml_with_plugins(tenants_dir, ["does.not.exist.module"])

    with pytest.raises(ImportError):
        create_app(
            workspace_yml=_yml(tenants_dir),
            tenant_id="test-tenant",
            enable_cron=False,
            enable_telegram_polling=False,
            enable_hybrid_runtime=False,
        )


def test_create_app_no_plugins_section_works(tenants_dir):
    """workspace.yml without `plugins:` is fine — bootstrap handles either."""
    from runspace.workspace import create_app

    # tenants_dir fixture didn't include plugins; just verify it boots.
    app = create_app(
        workspace_yml=_yml(tenants_dir),
        tenant_id="test-tenant",
        enable_cron=False,
        enable_telegram_polling=False,
        enable_hybrid_runtime=False,
    )
    assert app.state.tenant_id == "test-tenant"


# ─────────────────────────────────────────────────────────────────
# existing_gateway / existing_registry — migration path for tenants


def test_create_app_reuses_existing_gateway(tenants_dir):
    """When existing_gateway is supplied, bootstrap uses it instead of
    rebuilding the registry."""
    from runspace.workspace import create_app
    from runspace.workspace.backend import WorkspaceRegistry

    # Build the registry up-front like a tenant would at module level
    pre_registry = WorkspaceRegistry.from_tenants_dir(
        tenants_dir,
        slug_fn=lambda dn: dn,
    )
    pre_gw = pre_registry.get("test-tenant")
    assert pre_gw is not None

    app = create_app(
        workspace_yml=_yml(tenants_dir),
        tenant_id="test-tenant",
        existing_gateway=pre_gw,
        existing_registry=pre_registry,
        enable_cron=False,
        enable_telegram_polling=False,
        enable_hybrid_runtime=False,
    )

    # The exact same registry/gateway objects should be on app.state
    # (identity comparison, not equality)
    assert app.state.ws_registry is pre_registry
    assert app.state.workspace_gw is pre_gw
    assert app.state.tenant_id == "test-tenant"


def test_create_app_existing_gateway_resolves_tenant_from_gw_attr(tenants_dir):
    """If tenant_id arg is None and TENANT_ID env unset, fall back to
    existing_gateway.tenant_id when supplied — saves the caller a step."""
    from runspace.workspace import create_app
    from runspace.workspace.backend import WorkspaceRegistry

    pre_registry = WorkspaceRegistry.from_tenants_dir(
        tenants_dir,
        slug_fn=lambda dn: dn,
    )
    pre_gw = pre_registry.get("test-tenant")

    # Force tenant_id resolution to fall through to the gateway attribute
    saved_env = os.environ.pop("TENANT_ID", None)
    try:
        # Stamp tenant_id on the pre-built gateway (simulates how
        # WorkspaceGateway exposes it after loading workspace.yml)
        pre_gw.tenant_id = "test-tenant"
        app = create_app(
            workspace_yml=_yml(tenants_dir),
            tenant_id=None,
            existing_gateway=pre_gw,
            existing_registry=pre_registry,
            enable_cron=False,
            enable_telegram_polling=False,
            enable_hybrid_runtime=False,
        )
        assert app.state.tenant_id == "test-tenant"
    finally:
        if saved_env is not None:
            os.environ["TENANT_ID"] = saved_env
