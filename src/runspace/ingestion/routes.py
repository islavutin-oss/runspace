"""HTTP webhook routes for external channels."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)


def build_external_router(*, tenant_id: str, workspace_cfg: dict, app_registry, callbacks=None):
    """Return an APIRouter wired for this tenant's gateway. Caller
    (the WorkspaceGateway) attaches it under /api/external/...
    Each provider lives in its own module and is imported lazily.

    `callbacks` is an optional `CallbackHandlerRegistry`. When set, Telegram `callback_query` updates route
    through it; otherwise such updates are politely ignored.
    """
    r = APIRouter(prefix="/api/external", tags=["external"])

    @r.post("/{provider}/webhook")
    async def webhook(provider: str, request: Request):
        if provider == "telegram":
            from .telegram import handle_update, verify_secret

            secret_header = request.headers.get("x-telegram-bot-api-secret-token")
            ok = verify_secret(tenant_id, secret_header)
            if not ok:
                raise HTTPException(403, "bad secret_token")
            try:
                update = await request.json()
            except Exception:
                raise HTTPException(400, "invalid JSON body")
            try:
                result = await handle_update(
                    tenant_id=tenant_id,
                    workspace_cfg=workspace_cfg,
                    update=update,
                    app_registry=app_registry,
                    callbacks=callbacks,
                )
            except Exception as e:
                # Always 200 on application errors — Telegram retries
                # non-2xx responses, which would amplify a transient
                # bug into a flood.
                log.exception("[external/telegram] handle_update raised for %s", tenant_id)
                return {"ok": False, "error": str(e)[:300]}
            return {"ok": True, **result}

        # Future providers (whatsapp, email, slack) plug in here.
        raise HTTPException(404, f"unknown provider {provider!r}")

    return r
