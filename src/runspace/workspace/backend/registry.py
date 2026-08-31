"""WorkspaceRegistry — multi-tenant workspace dispatcher."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request

from .gateway import WorkspaceGateway

log = logging.getLogger(__name__)


class WorkspaceRegistry:
    """Manages multiple WorkspaceGateway instances for multi-tenant dispatch."""

    def __init__(self):
        self._workspaces: dict[str, WorkspaceGateway] = {}

    @classmethod
    def from_tenants_dir(
        cls,
        tenants_dir: str | Path,
        config_name: str = "workspace.yml",
        slug_fn=None,
        *,
        admin_dependency=None,
    ) -> WorkspaceRegistry:
        """Load all tenant workspaces from a directory.

        Each subdirectory with a workspace.yml gets its own gateway.
        The slug is derived from the directory name (first part before '-').
        Override slug_fn to customize: slug_fn(dir_name) -> slug.

        `admin_dependency` (optional FastAPI Depends) gates sensitive
        gateway routes (currently: pairing approve/revoke). Hosts pass
        their own auth dependency (e.g. `Depends(require_admin)`). When
        omitted, those routes are unauthenticated — only suitable for
        local dev or when the host enforces auth elsewhere.
        """
        reg = cls()
        tenants_path = Path(tenants_dir)

        if not tenants_path.is_dir():
            log.warning(f"[WorkspaceRegistry] Tenants dir not found: {tenants_path}")
            return reg

        for tenant_dir in sorted(tenants_path.iterdir()):
            ws_file = tenant_dir / config_name
            if not ws_file.exists():
                continue

            slug = slug_fn(tenant_dir.name) if slug_fn else tenant_dir.name.split("-")[0]
            try:
                gw = WorkspaceGateway.from_config(
                    str(ws_file),
                    admin_dependency=admin_dependency,
                )
                reg._workspaces[slug] = gw
                log.info(f"[WorkspaceRegistry] Loaded '{slug}' → {gw.name}")
            except Exception as e:
                log.error(f"[WorkspaceRegistry] Failed to load '{slug}': {e}")

        log.info(
            f"[WorkspaceRegistry] {len(reg._workspaces)} workspaces: {', '.join(reg._workspaces.keys())}"
        )
        return reg

    def register(self, slug: str, gateway: WorkspaceGateway) -> None:
        """Manually register a workspace."""
        self._workspaces[slug] = gateway

    def mount(self, app, *, prefix: str = "/api/workspace") -> int:
        """Expose every tenant's gateway routes on one app, dispatched by Host.

        A single-tenant host does `app.include_router(gateway.router)`. A
        multi-tenant host cannot: that binds every route to one gateway. The
        alternative — hand-writing a handful of routes that resolve the tenant
        themselves — is what hosts have actually been doing, and it silently
        exposes a fraction of the surface: channels, messages, uploads,
        pairings and the external-channel routes simply go missing.

        This builds one router whose paths and signatures come from a template
        gateway, and whose handlers re-resolve the tenant per request. Returns
        the number of routes mounted.
        """
        from fastapi import HTTPException, Request
        from fastapi.routing import APIRoute

        if not self._workspaces:
            log.warning("[WorkspaceRegistry] mount() called with no workspaces")
            return 0

        template = next(iter(self._workspaces.values()))
        router = APIRouter(prefix=prefix, tags=["workspace"])

        for route in template.router.routes:
            if not isinstance(route, APIRoute):
                continue
            # Path as declared on the gateway router, minus its own prefix.
            sub_path = route.path[len(template.router.prefix) :] or "/"
            router.routes.append(
                self._dispatching_route(route, sub_path, prefix, Request, HTTPException)
            )

        app.include_router(router)
        log.info(
            "[WorkspaceRegistry] mounted %d routes at %s for %d workspaces",
            len(router.routes),
            prefix,
            len(self._workspaces),
        )
        return len(router.routes)

    def _dispatching_route(self, template_route, sub_path, prefix, Request, HTTPException):
        """One route that resolves the tenant, then calls that tenant's handler.

        The endpoint's signature is copied from the template so FastAPI's
        dependency injection, body parsing and OpenAPI schema are unchanged;
        only `request` is added if the handler did not already take one.
        """
        import inspect
        import typing

        from fastapi.routing import APIRoute

        methods = sorted(template_route.methods - {"HEAD"})
        key = (template_route.path, frozenset(template_route.methods))
        sig = inspect.signature(template_route.endpoint)
        # `from __future__ import annotations` leaves these as strings, and the
        # wrapper below does not share the handler's module globals, so they
        # have to be resolved here or FastAPI cannot build a schema from them.
        try:
            hints = typing.get_type_hints(template_route.endpoint)
        except Exception:  # pragma: no cover - a handler with an exotic annotation
            hints = {}
        wants_request = any(
            hints.get(name, p.annotation) is Request for name, p in sig.parameters.items()
        )

        async def endpoint(request: Request, **kwargs):
            gw = self.resolve(request)
            if gw is None:
                raise HTTPException(404, "No workspace for this host")
            target = None
            for r in gw.router.routes:
                if isinstance(r, APIRoute) and (r.path, frozenset(r.methods)) == key:
                    target = r
                    break
            if target is None:  # pragma: no cover - gateways share one codebase
                raise HTTPException(500, f"Route {sub_path} missing on resolved workspace")
            if wants_request:
                kwargs["request"] = request
            result = target.endpoint(**kwargs)
            return await result if inspect.isawaitable(result) else result

        params = [
            p.replace(annotation=hints.get(name, p.annotation))
            for name, p in sig.parameters.items()
            if hints.get(name, p.annotation) is not Request
        ]
        request_param = inspect.Parameter(
            "request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request
        )
        endpoint.__signature__ = inspect.Signature([request_param] + params)
        endpoint.__name__ = template_route.endpoint.__name__
        endpoint.__doc__ = template_route.endpoint.__doc__

        return APIRoute(
            prefix + sub_path if sub_path != "/" else prefix,
            endpoint,
            methods=methods,
            name=template_route.name,
            response_model=template_route.response_model,
            dependencies=list(template_route.dependencies),
        )

    def resolve(self, request: Request) -> WorkspaceGateway | None:
        """Resolve tenant workspace from HTTP headers (Host/X-Forwarded-Host/Referer).

        Match on the leftmost host label (subdomain), not arbitrary substring.
        Reason: every *.example.com host contains "example" — substring match
        always wins the apex tenant. Compare host's first label exactly to slug;
        fall back to referer path prefix `/{slug}/` for path-based routing.
        """
        host = (
            request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        ).lower()
        referer = request.headers.get("referer", "").lower()
        host_label = host.split(":")[0].split(".")[0]

        for slug, gw in self._workspaces.items():
            if (
                host_label == slug
                or f"/{slug}/" in referer
                or referer.rstrip("/").endswith(f"/{slug}")
            ):
                return gw

        # Default to first workspace
        return next(iter(self._workspaces.values()), None)

    def resolve_slug(self, request: Request) -> tuple[str | None, WorkspaceGateway | None]:
        """Resolve tenant slug and workspace from HTTP headers."""
        host = (
            request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        ).lower()
        referer = request.headers.get("referer", "").lower()
        host_label = host.split(":")[0].split(".")[0]

        for slug, gw in self._workspaces.items():
            if (
                host_label == slug
                or f"/{slug}/" in referer
                or referer.rstrip("/").endswith(f"/{slug}")
            ):
                return slug, gw

        # Default to first
        if self._workspaces:
            first_slug = next(iter(self._workspaces))
            return first_slug, self._workspaces[first_slug]
        return None, None

    def get(self, slug: str) -> WorkspaceGateway | None:
        """Get workspace by slug."""
        return self._workspaces.get(slug)

    @property
    def slugs(self) -> list[str]:
        return list(self._workspaces.keys())

    def __len__(self) -> int:
        return len(self._workspaces)

    def __contains__(self, slug: str) -> bool:
        return slug in self._workspaces
