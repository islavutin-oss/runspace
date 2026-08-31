"""High-level FastAPI bootstrap for runspace tenants."""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .registry import WorkspaceRegistry

log = logging.getLogger(__name__)

StartupHook = Callable[[FastAPI], Awaitable[None]]
ShutdownHook = Callable[[FastAPI], Awaitable[None]]
MiddlewareSpec = tuple[type, dict[str, Any]]  # (middleware_class, kwargs)


def _load_plugins(workspace_yml: Path) -> dict[str, list]:
    """Read workspace.yml's `plugins:` list, import each module, collect
    contributed symbols.

    A plugin module may define any of:
      - `router` or `routers` (APIRouter or list[APIRouter])
      - `cron_executors` (list of executor instances)
      - `startup_hooks` (list of async (app)->None)
      - `shutdown_hooks` (list of async (app)->None)
      - `middlewares` (list of (Middleware, kwargs) tuples)

    Missing symbols are skipped silently. Modules that fail to import
    raise loudly — better fail boot than mount a half-broken app.
    """
    collected: dict[str, list] = {
        "routers": [],
        "cron_executors": [],
        "startup_hooks": [],
        "shutdown_hooks": [],
        "middlewares": [],
    }
    try:
        cfg = yaml.safe_load(workspace_yml.read_text()) or {}
    except yaml.YAMLError:
        log.exception("[bootstrap] workspace.yml unreadable")
        return collected

    plugin_modules = cfg.get("plugins") or []
    if not plugin_modules:
        return collected

    for mod_name in plugin_modules:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            log.exception("[bootstrap] plugin %s failed to import", mod_name)
            raise

        # Routers
        r = getattr(mod, "router", None)
        if r is not None:
            collected["routers"].append(r)
        rs = getattr(mod, "routers", None)
        if rs:
            collected["routers"].extend(rs)

        # Other symbol lists — extend if present
        for key in ("cron_executors", "startup_hooks", "shutdown_hooks", "middlewares"):
            items = getattr(mod, key, None)
            if items:
                collected[key].extend(items)

        log.info("[bootstrap] plugin loaded: %s", mod_name)

    return collected


def create_app(
    workspace_yml: str | Path,
    tenant_id: str | None = None,
    *,
    existing_gateway=None,
    existing_registry=None,
    extra_routers: Sequence[APIRouter] = (),
    extra_middlewares: Sequence[MiddlewareSpec] = (),
    extra_startup_hooks: Sequence[StartupHook] = (),
    extra_shutdown_hooks: Sequence[ShutdownHook] = (),
    extra_executors: Sequence[Any] = (),
    cors_origins: Sequence[str] = ("*",),
    cors_allow_credentials: bool = True,
    enable_cron: bool = True,
    enable_telegram_polling: bool = True,
    enable_hybrid_runtime: bool = True,
    title: str = "runspace tenant",
) -> FastAPI:
    """Build a fully-wired FastAPI app from a tenant's workspace.yml.

    The returned app exposes runspace state on `app.state`:
      app.state.workspace_gw      WorkspaceGateway
      app.state.ws_registry       WorkspaceRegistry (single tenant or multi)
      app.state.tenant_id         resolved tenant id (env or workspace.yml)
      app.state.cron_service      services.cron.cron_service singleton
                                  (None when enable_cron=False)

    Tenant startup hooks run AFTER bootstrap-managed wiring but BEFORE
    cron initialize() — so a hook can register additional executors via
    `app.state.cron_service.register_executor(…)` and they'll be in
    place when cron.initialize() loads the job set.
    """

    # ─────────────────────────────────────────────────────────────────
    # Phase 0: load plugins from workspace.yml#plugins (zero-code path)
    # ─────────────────────────────────────────────────────────────────
    yml = Path(workspace_yml).resolve()
    if not yml.exists():
        raise FileNotFoundError(f"workspace.yml not found: {yml}")

    plugins = _load_plugins(yml)
    # Concatenate plugin contributions with explicit-arg contributions.
    # Explicit args win on conflicts (last-write semantics in mount order).
    extra_routers = [*plugins["routers"], *extra_routers]
    extra_executors = [*plugins["cron_executors"], *extra_executors]
    extra_startup_hooks = [*plugins["startup_hooks"], *extra_startup_hooks]
    extra_shutdown_hooks = [*plugins["shutdown_hooks"], *extra_shutdown_hooks]
    extra_middlewares = [*plugins["middlewares"], *extra_middlewares]

    # ── Reuse pre-built gateway + registry if supplied (migration path)
    # ── Tenants with a lot of module-level code that already builds the
    # ── gateway/registry at import time (e.g. for routes that read those
    # ── globals) can pass them in here so we don't duplicate work.
    if existing_registry is not None and existing_gateway is not None:
        ws_registry = existing_registry
        workspace_gw = existing_gateway
        resolved_tenant = (
            tenant_id
            or os.environ.get("TENANT_ID", "").strip()
            or getattr(workspace_gw, "tenant_id", None)
            or yml.parent.name
        )
    else:
        # WorkspaceRegistry.from_tenants_dir scans a parent directory for
        # tenant subdirs; if we got pointed at a single tenant's workspace.yml
        # we walk up to the tenants/ root.
        tenants_root = yml.parent.parent if yml.parent.name != "tenants" else yml.parent
        ws_registry = WorkspaceRegistry.from_tenants_dir(
            tenants_root,
            slug_fn=lambda dn: dn,
        )

        # Pick the tenant: explicit > env > first one in registry
        resolved_tenant = tenant_id or os.environ.get("TENANT_ID", "").strip() or yml.parent.name
        workspace_gw = ws_registry.get(resolved_tenant)
        if workspace_gw is None:
            raise RuntimeError(
                f"tenant '{resolved_tenant}' not found in registry "
                f"(scanned {tenants_root}); tenants discovered: "
                f"{sorted(ws_registry.slugs)}"
            )

    # ─────────────────────────────────────────────────────────────────
    # Phase 2: hybrid agent-runtime wiring (no-op unless any agent has
    #          runtime: openclaw in workspace.yml)
    # ─────────────────────────────────────────────────────────────────
    if enable_hybrid_runtime:
        try:
            _try_wire_hybrid_runtime(workspace_gw, resolved_tenant)
        except Exception:
            # Never break startup over the runtime hedge.
            log.exception(
                "[bootstrap] hybrid runtime wiring failed — falling back to agentino-only registry"
            )

    # ─────────────────────────────────────────────────────────────────
    # Phase 3: FastAPI app + middleware
    # ─────────────────────────────────────────────────────────────────
    app = FastAPI(
        title=title,
        lifespan=_make_lifespan(
            workspace_gw=workspace_gw,
            ws_registry=ws_registry,
            tenant_id=resolved_tenant,
            extra_startup_hooks=extra_startup_hooks,
            extra_shutdown_hooks=extra_shutdown_hooks,
            extra_executors=extra_executors,
            enable_cron=enable_cron,
            enable_telegram_polling=enable_telegram_polling,
        ),
    )

    app.state.workspace_gw = workspace_gw
    app.state.ws_registry = ws_registry
    app.state.tenant_id = resolved_tenant
    app.state.cron_service = None  # set by lifespan

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_credentials=cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for mw_class, mw_kwargs in extra_middlewares:
        app.add_middleware(mw_class, **mw_kwargs)

    # Mount the gateway router (workspace, channels, agents, dashboards…)
    if workspace_gw.router is not None:
        app.include_router(workspace_gw.router)

    for r in extra_routers:
        app.include_router(r)

    return app


def _try_wire_hybrid_runtime(workspace_gw, tenant: str) -> None:
    """Wrap workspace_gw.registry with a RouterRegistryShim if any agent
    in the workspace has `runtime: openclaw`.

    Imports are deferred so tenants that don't ship the hybrid plumbing
    (apps.api.agent_runtime in acme's tree) just skip the whole
    branch via ImportError.
    """
    try:
        from apps.api.agent_runtime import AgentRuntimeRouter  # type: ignore
        from apps.api.agent_runtime.registry_shim import (  # type: ignore
            RouterRegistryShim,
            build_agentino_invoker,
        )
        from apps.api.agent_runtime.runtimes import (  # type: ignore
            AgentinoRuntime,
            OpenclawRuntime,
        )
        from apps.api.agent_runtime.workspace_loader import default_loader  # type: ignore
    except ImportError:
        # Tenant doesn't ship the hybrid runtime modules — skip silently.
        return

    loader = default_loader()
    cfgs = loader.load(tenant)
    has_openclaw = any(getattr(c, "runtime", None) == "openclaw" for c in cfgs.values())
    if not has_openclaw:
        return

    agentino_runtime = AgentinoRuntime(
        invoker=build_agentino_invoker(workspace_gw.registry),
    )
    openclaw_path = (
        Path(__file__).resolve().parents[3] / "openclaw-plugins" / "openclaw.config.json"
    )
    openclaw_runtime = OpenclawRuntime(
        config_path=openclaw_path,
        profile=os.environ.get("OPENCLAW_PROFILE", "dev"),
        timeout_s=int(os.environ.get("OPENCLAW_TIMEOUT_S", "60")),
    )
    router = AgentRuntimeRouter(
        runtimes={"agentino": agentino_runtime, "openclaw": openclaw_runtime},
        workspace_loader=loader,
    )
    workspace_gw.registry = RouterRegistryShim(
        workspace_gw.registry,
        router,
        tenant_id=tenant,
    )
    openclaw_agents = sorted(a for a, c in cfgs.items() if c.runtime == "openclaw")
    log.info("[bootstrap] hybrid mode active — openclaw routes: %s", openclaw_agents)


def _make_lifespan(
    *,
    workspace_gw,
    ws_registry,
    tenant_id: str,
    extra_startup_hooks: Sequence[StartupHook],
    extra_shutdown_hooks: Sequence[ShutdownHook],
    extra_executors: Sequence[Any],
    enable_cron: bool,
    enable_telegram_polling: bool,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── Step 1: cron service ─────────────────────────────────
        cron_service = None
        if enable_cron:
            try:
                # services.cron is a acme/runspace-product convention.
                # Tenants that don't include it can disable via enable_cron=False.
                from services.cron import cron_service as loaded_cron  # type: ignore

                cron_service = loaded_cron
                app.state.cron_service = cron_service

                # AppRegistry factory so cron handlers can invoke agents.
                cron_service.set_app_registry_factory(lambda _tid: workspace_gw.registry)

                # Default executors — RoutineExecutor (agent-driven) +
                # any extras the tenant passed in.
                from services.cron.delivery import default_router  # type: ignore
                from services.cron.executors import RoutineExecutor  # type: ignore

                cron_service.register_executor(
                    RoutineExecutor(
                        app_registry_factory=lambda _tid: workspace_gw.registry,
                        delivery_router=default_router(),
                    )
                )
                for ex in extra_executors:
                    cron_service.register_executor(ex)

                # File-as-truth routines via CompositeJobStore.
                if getattr(workspace_gw, "routines_store", None):
                    from services.cron.store import CompositeJobStore  # type: ignore

                    cron_service.scheduler._store = CompositeJobStore(
                        fallback=cron_service.scheduler._store,
                        routines_store=workspace_gw.routines_store,
                    )

                await cron_service.initialize()
            except ImportError:
                log.info("[bootstrap] services.cron not available — skipping cron wiring")

        # ── Step 2: tenant startup hooks (run after cron is ready)
        for hook in extra_startup_hooks:
            await hook(app)

        # ── Step 3: ingestion / channel polling ────────────────
        if enable_telegram_polling:
            try:
                # workspace_gw exposes start_polling() when workspace.yml
                # has `messaging.<bot>.transport: polling` —
                start = getattr(workspace_gw, "start_polling", None)
                if start is not None:
                    await start()
                    log.info("[bootstrap] telegram polling started")
            except Exception:
                log.exception("[bootstrap] failed to start telegram polling")

        try:
            yield
        finally:
            # ── Shutdown ───────────────────────────────────────────
            for hook in extra_shutdown_hooks:
                try:
                    await hook(app)
                except Exception:
                    log.exception("[bootstrap] shutdown hook failed")

            if cron_service is not None:
                try:
                    await cron_service.shutdown()
                except Exception:
                    log.exception("[bootstrap] cron shutdown failed")

            # Stop polling transports if started.
            try:
                stop = getattr(workspace_gw, "stop_polling", None)
                if stop is not None:
                    await stop()
            except Exception:
                log.exception("[bootstrap] failed to stop telegram polling")

    return lifespan


__all__ = ["create_app", "StartupHook", "ShutdownHook", "MiddlewareSpec"]
