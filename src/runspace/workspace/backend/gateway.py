"""Workspace Gateway — one router to rule them all."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from .activity_log import ActivityLog
from .app_registry import AgentApp, AppRegistry, request_user_name
from .attachments import (
    _collect_file_attachments,
    _ensure_attachments_referenced,
    _resolve_files,
)
from .media import _build_transcriber, _resolve_env_vars
from .models import (
    AttachmentInput,  # noqa: F401  re-exported for consumers
    ChatRequest,
    ChatResponse,
    FileAttachmentResponse,  # noqa: F401  re-exported for consumers
    RoutineCreateRequest,
)

# MessagingService imported lazily (requires supabase SDK)

log = logging.getLogger(__name__)


class WorkspaceGateway:
    """Central gateway for workspace API."""

    def __init__(
        self, name: str = "Workspace", tenant_id: str | None = None, admin_dependency: Any = None
    ):
        self.name = name
        self.tenant_id = tenant_id
        # Chat history is in-memory unless a deployment opts in. Dialog mode
        # keeps only the thread list in the browser and addresses the messages
        # by session_id, so without a persistent store every restart leaves the
        # sidebar listing threads that open empty.
        from .history_sqlite import history_store_from_env

        self.registry = AppRegistry(
            workspace_name=name,
            tenant_id=tenant_id,
            history_store=history_store_from_env(tenant_id or "default"),
        )
        # User info set after config load
        self.activity = ActivityLog()
        # Optional FastAPI dependency that enforces admin/owner-only
        # access on sensitive routes (pairing approve/revoke). Hosts
        # pass `Depends(require_admin)` here. Default `None` keeps the
        # routes wide-open — same behaviour as before this option
        # existed; hosts that want auth must opt in.
        self._admin_dependency = admin_dependency
        self.router = APIRouter(prefix="/api/workspace", tags=["workspace"])
        self._routines_file: Path | None = None
        # Populated by from_config when workspace.yml declares `routines:`.
        # Self-discovered — apps don't have to wire this up. Implements
        # the JobStore protocol so acme's cron picker can use it
        # straight, no DB rows needed.
        self.routines_store = None
        # Registry of inline-button callback handlers.
        # Apps register handlers by `callback_data` prefix; the
        # external_channels webhook hands callback_query updates here.
        # Empty by default — only used when an app wires inline UIs.
        from runspace.ingestion.transport import CallbackHandlerRegistry

        self.callbacks = CallbackHandlerRegistry()
        self._scheduler = None
        self._channels: list[dict] = []
        self._settings_schema: list[dict] = []
        self._suggestions: list[str] = []
        self._user_name: str = "User"
        self._user_role: str = "Owner"
        self._icon: str = "🤖"
        self._brand_color: str = "#6B7280"
        self._sidebar_color: str = "#1a1a2e"
        self._users: dict = {}
        self._messaging: Any = None  # MessagingService (lazy import)
        # Optional second router for external channels. Set by
        # from_config() when workspace.yml declares external_channels.
        self.external_router: Any = None
        # Strong references to in-flight chat-stream background tasks.
        # /chat/stream spawns its agent run in a separate task so a
        self._inflight_chat_tasks: set[asyncio.Task] = set()
        self._setup_routes()

    def _mirror_to_documents_table(
        self,
        *,
        tenant_id: str,
        file_id: str,
        original_name: str,
        size_bytes: int,
        content_type: str,
    ) -> None:
        """Insert a row in the platform's `documents` table for a
        chat-uploaded file so it shows up on the Documents page.

        The chat path stores bytes in `protocols.FileStorage` (Supabase
        Storage bucket on prod). The Documents page reads the
        `documents` table. Without this bridge, files uploaded through
        chat are invisible to the Documents UI even though they exist
        in storage. We write a metadata row pointing at the same
        bytes, with `source='chat'` so it's distinguishable from
        upload-button uploads.

        Best-effort: caller wraps this in try/except. Database not
        available, table missing, or RLS denial all log-and-continue.
        """
        # Build a Supabase client directly so this module doesn't
        # have to import the host platform's helpers (would tie
        # runspace to acme's services/ layout).
        import os

        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
        if not url or not key:
            return  # No Supabase config → nothing to mirror to
        try:
            from supabase import create_client

            create_client(url, key)
        except Exception:
            return
        # The bucket / storage_path here describe the FileStorage
        # location, not the workspace-files bucket. Documents UI
        row = {
            "tenant_id": tenant_id,
            "bucket": "chat",
            "storage_path": file_id,
            "filename": original_name,
            "mime": content_type,
            "size_bytes": size_bytes,
            "content_hash": file_id.split("_", 1)[0] if "_" in file_id else "",
            "source": "chat",
            "created_by": None,
            "title": original_name.rsplit(".", 1)[0],
            "description": None,
        }
        try:
            # DocumentStore over protocols.Store.
            from runspace.helpers.documents.store import get_document_store

            get_document_store(tenant_id).insert(row)
            log.info("[Upload] mirrored to documents table: %s", original_name)
        except Exception as e:
            log.warning("[Upload] documents insert failed: %s", e)

    def configure(
        self, *, history_store=None, context_enricher=None, message_enricher=None
    ) -> None:
        """Formal API for products to customize registry behavior.

        Use this instead of reaching into self.registry internals.
        """
        if history_store is not None:
            self.registry._history_store = history_store
        if context_enricher is not None:
            self.registry.context_enricher = context_enricher
        if message_enricher is not None:
            self.registry.message_enricher = message_enricher

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        base_dir: str | Path | None = None,
        *,
        admin_dependency: Any = None,
    ) -> WorkspaceGateway:
        """Create gateway from workspace.yml config.

        `admin_dependency` is an optional FastAPI Depends used on
        sensitive routes (pairing approve/revoke). Hosts pass their
        own auth dependency (e.g. `Depends(require_admin)`); when
        omitted, those routes are unauthenticated — only suitable
        for local dev or when the host enforces auth elsewhere.
        """
        path = Path(config_path)
        base = Path(base_dir) if base_dir else path.parent

        with open(path) as f:
            config = yaml.safe_load(f) or {}

        # Stamp the resolved tenant base dir into the config dict so
        # downstream consumers (external_channels, routines store, …)
        # can locate sibling files (.pairings.json, routines.yml,
        # .telegram-offset.json) without re-parsing the file or
        # threading the path through every helper signature.
        config["_base_dir"] = str(base)

        ws_name = config.get("name", "Workspace")
        # Derive tenant_id from config or directory name
        tenant_id = config.get("tenant_id") or path.parent.name
        gw = cls(name=ws_name, tenant_id=tenant_id, admin_dependency=admin_dependency)
        # Stash the raw config so non-mainline routes (Telegram webhook,
        # external_channels) can read tenant-scoped sub-blocks
        # without re-parsing the YAML file.
        gw._config = config
        # Stash the path so `reload_config()` can re-read it without
        # the caller having to remember where it came from.
        gw._workspace_cfg_path = str(path)
        gw._icon = config.get("icon", "🤖")
        gw._brand_color = config.get("brand_color", "#6B7280")
        gw._sidebar_color = config.get("sidebar_color", "#1a1a2e")

        # Read workspace-level providers (fallback for agents without agents.yml)
        providers = config.get("providers", {})
        if providers:
            # Use the first provider as default
            first = next(iter(providers.values()), {})
            gw.registry.default_provider = {
                "base_url": _resolve_env_vars(first.get("base_url", "")),
                "api_key": _resolve_env_vars(first.get("api_key", "")),
                "provider": first.get("provider", ""),
            }

        # Register apps
        for app_id, app_cfg in config.get("apps", {}).items():
            soul_path = str(base / app_cfg["soul"]) if app_cfg.get("soul") else None
            tools_dir = str(base / app_cfg["tools"]) if app_cfg.get("tools") else None
            # shared_tools can be a string (single dir) or a list of dirs
            # YAML key stays `shared_tools` for backwards-compat with tenant
            # configs; only the Python package rename to `agent_tools` is internal.
            raw_shared = app_cfg.get("shared_tools")
            if isinstance(raw_shared, str):
                shared_tools_dirs = [str(base / raw_shared)]
            elif isinstance(raw_shared, list):
                shared_tools_dirs = [str(base / d) for d in raw_shared]
            else:
                shared_tools_dirs = []

            # Force a tool call on the first turn (workspace.yml
            # `require_tool_use: true`). Matches the old-prod booking agent.
            require_tool_use = bool(app_cfg.get("require_tool_use", False))

            # Named std-tool bundles the agent opts into (workspace.yml
            # `std_tools: [documents, web]`). No implicit std load — each
            # agent declares what it needs. Accepts a string or list.
            raw_std = app_cfg.get("std_tools")
            if isinstance(raw_std, str):
                std_bundles = [raw_std]
            elif isinstance(raw_std, list):
                std_bundles = [str(b) for b in raw_std]
            else:
                std_bundles = []

            # Collect gate/sanitize/security config if present
            gates_config = app_cfg.get("gates")
            response_filter_cfg = app_cfg.get("response_filter")
            max_turns = int(app_cfg.get("max_turns", 10))

            gw.registry.register(
                AgentApp(
                    id=app_id,
                    name=app_cfg.get("name", app_id.capitalize()),
                    role=app_cfg.get("role", ""),
                    avatar=app_cfg.get("avatar", "🤖"),
                    color=app_cfg.get("color", "#6B7280"),
                    group=app_cfg.get("group", "default"),
                    suggestions=list(app_cfg.get("suggestions") or []),
                    type=app_cfg.get("type", "agentino"),
                    enabled=app_cfg.get("enabled", True),
                    soul_path=soul_path,
                    tools_dir=tools_dir,
                    shared_tools_dirs=shared_tools_dirs,
                    std_bundles=std_bundles,
                    require_tool_use=require_tool_use,
                    model=app_cfg.get("model"),
                    endpoint=_resolve_env_vars(app_cfg.get("endpoint", "")),
                    gates_config=gates_config,
                    response_filter=response_filter_cfg,
                    max_turns=max_turns,
                    workspace_path=str(base),
                )
            )

        # Channels
        gw._channels = config.get("channels", [])

        # Settings schema
        gw._settings_schema = config.get("settings", {}).get("sections", [])

        # Users
        users = config.get("users", {})
        if users:
            default_user = next(
                (u for u in users.values() if u.get("default")), next(iter(users.values()), {})
            )
            gw._user_name = default_user.get("name", "User")
            gw._user_role = default_user.get("role", "Owner")
        else:
            # Legacy single-user format
            gw._user_name = config.get("user", {}).get("name", "User")
            gw._user_role = config.get("user", {}).get("role", "Owner")
        gw._users = users

        # Pass user context to registry so agents know who they're talking to
        gw.registry._user_name = gw._user_name
        gw.registry._user_role = gw._user_role

        # Routines: file-as-truth. When the workspace.yml
        # declares `routines: <path>`, we stand up a JobStore-shaped
        if config.get("routines"):
            gw._routines_file = base / config["routines"]
            try:
                from .routines_store import WorkspaceRoutinesStore

                gw.routines_store = WorkspaceRoutinesStore(
                    routines_file=gw._routines_file,
                    tenant_id=tenant_id or "",
                    registry=gw.registry,
                )
            except Exception as e:
                # Don't crash gateway boot — log and fall through; the
                # cron picker will see routines_store=None and use its
                # next-best backend. Misconfigured yaml shouldn't take
                # the whole API down.
                log.warning("[routines_store] init failed: %s", e)

        # Demo conversation seed
        gw._demo = config.get("demo")
        # Opening suggestions for a dialog client. Config, not code:
        # a single-agent workspace wants its own three questions, and
        # the generic defaults teach a visitor nothing about it.
        gw._suggestions = config.get("suggestions") or []

        # Audio transcription
        gw._transcriber = _build_transcriber(config.get("audio"))

        # Messaging. Supabase when it is configured, otherwise a local SQLite
        # file — channels are the workspace's main surface, so having none
        # unless a hosted database happens to be wired up made a fresh install
        # look broken rather than unconfigured.
        try:
            import os as _os

            if _os.environ.get("SUPABASE_URL") and _os.environ.get("SUPABASE_KEY"):
                from .messaging import MessagingService

                gw._messaging = MessagingService(tenant_id=tenant_id)
                backend = "supabase"
            else:
                from .messaging_sqlite import SqliteMessagingService

                gw._messaging = SqliteMessagingService(tenant_id=tenant_id)
                backend = "sqlite"

            agents_map = {a["id"]: a for a in gw.registry.list_apps()}
            gw._messaging.ensure_default_channels(gw._channels, agents_map)
            log.info("[Messaging] %s backend for tenant=%s", backend, tenant_id)
        except Exception as e:
            log.warning("[Messaging] Not available: %s", e)

        # External-channels webhook router — generic /api/external/{provider}/webhook.
        # Mounted only when the workspace.yml declares any external_channels,
        # so deployments without external transports don't pay for the route.
        if config.get("external_channels"):
            try:
                from runspace.ingestion.routes import build_external_router

                gw.external_router = build_external_router(
                    tenant_id=tenant_id,
                    workspace_cfg=config,
                    app_registry=gw.registry,
                    callbacks=gw.callbacks,
                )
            except Exception as e:
                log.warning("[ExternalChannels] Router build failed: %s", e)

        # Stash refs the host app needs to
        # decide whether to start a polling transport at lifespan
        gw._workspace_cfg = config

        return gw

    async def start_external_channel_transports(self) -> None:
        """Spin up long-poll transports for every Telegram bot
        declared in workspace.yml's `messaging.telegram_bots`.

        Called from the host app's FastAPI lifespan after the event
        loop is running, AND from `reload_config()` to pick up bots
        added via hot-reload. Webhook-mode bots are skipped here
        (the route is wired by `external_router`); polling-mode
        bots get one independent `TelegramPollingTransport` each.

        Each bot has its own offset file, its own pairing state, and
        its own bot identity cache — multi-bot tenants run N parallel
        long-poll loops with no shared state between bots.

        Idempotent on bot.name: existing transports keep running;
        only newly-declared bots start. Removed bots are NOT stopped
        here (use `stop_external_channel_transports` for full reset).
        """
        if getattr(self, "_external_transports", None) is None:
            self._external_transports = []
        existing_names = {getattr(t, "_bot_name", None) for t in self._external_transports}
        cfg = getattr(self, "_workspace_cfg", None) or {}
        from runspace.ingestion.pairing import resolve_telegram_bots
        from runspace.ingestion.transport import pick_telegram_transport_mode

        bots = resolve_telegram_bots(cfg)
        if not bots:
            return

        from pathlib import Path as _P

        from runspace.ingestion.polling import TelegramPollingTransport
        from runspace.ingestion.telegram import _bot_token, handle_update

        offset_dir = _P(getattr(self, "_routines_file", "") or ".").parent
        callbacks = getattr(self, "callbacks", None)
        started: list[str] = []
        for bot in bots:
            bot_name = bot.get("name") or "default"
            if bot_name in existing_names:
                continue  # already running, hot-reload safe
            mode = pick_telegram_transport_mode(cfg, self.tenant_id or "", bot)
            if mode != "polling":
                log.info("[ExternalChannels] bot %s in webhook mode (no polling task)", bot_name)
                continue
            try:
                token = _bot_token(cfg, bot)
                if not token:
                    log.warning(
                        "[ExternalChannels] bot %s polling requested but no token", bot_name
                    )
                    continue
                bot_offset_path = offset_dir / bot.get(
                    "offset_filename", f".telegram-offset-{bot_name}.json"
                )

                # Closure capture: bind `bot` per loop iteration so each
                # transport's lambda dispatches with ITS bot_config, not
                # the last one. Default-arg pattern is the canonical fix.
                def _make_handle(bot_cfg):
                    return lambda upd: handle_update(
                        tenant_id=self.tenant_id or "",
                        workspace_cfg=cfg,
                        update=upd,
                        app_registry=self.registry,
                        callbacks=callbacks,
                        bot_config=bot_cfg,
                    )

                transport = TelegramPollingTransport(
                    tenant_id=f"{self.tenant_id}:{bot_name}",
                    bot_token=token,
                    offset_path=bot_offset_path,
                    handle=_make_handle(bot),
                )
                # Tag the transport with the bot's name so future
                # hot-reload calls can detect "already running".
                transport._bot_name = bot_name
                await transport.start()
                self._external_transports.append(transport)
                started.append(bot_name)
            except Exception as e:
                log.warning("[ExternalChannels] start failed for bot %s: %s", bot.get("name"), e)
        if started:
            log.info(
                "[ExternalChannels] telegram polling started for tenant %s bots=%s",
                self.tenant_id,
                started,
            )

    def reload_config(self) -> dict:
        """Re-read workspace.yml from disk and mutate `_workspace_cfg`
        IN PLACE.

        Why mutate in place rather than replace: the running polling
        task's `handle_update` lambda captures `cfg` by reference. If
        we replaced `_workspace_cfg = new_dict`, the closure would
        keep pointing at the old dict. By clearing keys and re-
        populating the SAME dict object, the closure sees the new
        state on its next inbound message — no asyncio task respawn
        needed. external_channels bindings, dmAgent / dmPolicy
        changes per-bot, allowFrom updates — all propagate
        instantly.

        For NEW bots (added entries in `messaging.telegram_bots`),
        the caller should also `await
        start_external_channel_transports()` after — the helper is
        idempotent and only starts the new ones.

        Returns a small diff summary the caller can echo to the UI.
        """
        cfg = getattr(self, "_workspace_cfg", None)
        if cfg is None:
            raise RuntimeError("gateway has no _workspace_cfg loaded")
        path = getattr(self, "_workspace_cfg_path", None)
        if not path:
            raise RuntimeError(
                "gateway has no workspace.yml path stashed; "
                "was the gateway built via from_config()?"
            )
        with open(path) as f:
            new_data = yaml.safe_load(f) or {}
        # Re-stamp _base_dir (lost when we re-read the raw yaml).
        base_dir = cfg.get("_base_dir")
        new_data["_base_dir"] = base_dir

        from runspace.ingestion.pairing import resolve_telegram_bots

        old_bindings = list(cfg.get("external_channels") or [])
        old_bots = [b.get("name") for b in resolve_telegram_bots(cfg)]
        old_dm_agents = {b.get("name"): b.get("dmAgent") for b in resolve_telegram_bots(cfg)}

        # Mutate in place: clear then re-populate.
        cfg.clear()
        cfg.update(new_data)

        new_bindings = list(cfg.get("external_channels") or [])
        new_bots = [b.get("name") for b in resolve_telegram_bots(cfg)]
        new_dm_agents = {b.get("name"): b.get("dmAgent") for b in resolve_telegram_bots(cfg)}

        log.info(
            "[ReloadConfig] tenant %s bindings=%d→%d bots=%s→%s",
            self.tenant_id,
            len(old_bindings),
            len(new_bindings),
            old_bots,
            new_bots,
        )

        return {
            "external_channels": {
                "before": len(old_bindings),
                "after": len(new_bindings),
            },
            "telegram_bots": {
                "added": [b for b in new_bots if b not in old_bots],
                "removed": [b for b in old_bots if b not in new_bots],
                "kept": [b for b in new_bots if b in old_bots],
            },
            "dmAgent_changes": {
                name: {"before": old_dm_agents.get(name), "after": new_dm_agents.get(name)}
                for name in (set(old_dm_agents) | set(new_dm_agents))
                if old_dm_agents.get(name) != new_dm_agents.get(name)
            },
        }

    async def stop_external_channel_transports(self) -> None:
        for t in getattr(self, "_external_transports", []) or []:
            try:
                await t.stop()
            except Exception as e:
                log.warning("[ExternalChannels] transport stop failed: %s", e)
        self._external_transports = []

    def _setup_routes(self):
        r = self.router

        @r.get("/settings")
        async def get_settings():
            """Current values behind the settings screen.

            The frontend has fetched this since the settings screen existed;
            nothing served it, so every workspace rendered empty fields.
            """
            from .settings_store import SettingsStore

            store = SettingsStore(self.tenant_id or "default")
            return store.load(self._settings_schema)

        @r.put("/settings")
        async def put_settings(body: dict):
            from .settings_store import SettingsStore

            store = SettingsStore(self.tenant_id or "default")
            try:
                return store.save(body or {}, self._settings_schema)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e

        @r.get("/config")
        async def get_config():
            """Full workspace config for the frontend — apps, channels, settings, user."""
            result = {
                "name": self.name,
                "tenant_id": self.tenant_id,
                "icon": self._icon,
                "brand_color": self._brand_color,
                "sidebar_color": self._sidebar_color,
                "apps": self.registry.list_apps(),
                "channels": self._channels,
                "settings_schema": self._settings_schema,
                "suggestions": self._suggestions,
                "user": {"name": self._user_name, "role": self._user_role},
                "users": self._users,
            }
            if self._demo:
                # Enrich demo messages with agent metadata
                apps_map = {a["id"]: a for a in result["apps"]}
                for msg in self._demo.get("messages", []):
                    if msg.get("botId") and msg["botId"] in apps_map:
                        app = apps_map[msg["botId"]]
                        msg.setdefault("botName", app["name"])
                        msg.setdefault("botColor", app["color"])
                        msg.setdefault("botAvatar", app.get("avatar", ""))
                for replies in self._demo.get("threads", {}).values():
                    for msg in replies:
                        if msg.get("botId") and msg["botId"] in apps_map:
                            app = apps_map[msg["botId"]]
                            msg.setdefault("botName", app["name"])
                            msg.setdefault("botColor", app["color"])
                            msg.setdefault("botAvatar", app.get("avatar", ""))
                result["demo"] = self._demo
            return result

        @r.get("/apps")
        async def list_apps():
            return {"apps": self.registry.list_apps()}

        @r.post("/chat", response_model=ChatResponse)
        async def chat(body: ChatRequest):
            app = self.registry.get(body.resolved_app_id)
            if not app:
                raise HTTPException(404, f"App '{body.resolved_app_id}' not found")

            # Resolve files (uploaded + legacy base64) → context text + optional audio
            message = body.message
            file_context, audio_b64, audio_mime = _resolve_files(
                body.file_ids, body.attachments, tenant_id=self.tenant_id
            )

            # Handle audio (from file upload or legacy base64)
            audio_b64 = audio_b64 or body.media_base64
            audio_mime = audio_mime or body.media_mime
            if audio_b64:
                log.info(
                    "[Chat] audio=%d bytes mime=%s transcriber=%s",
                    len(audio_b64),
                    audio_mime,
                    "yes" if self._transcriber else "no",
                )
            if audio_b64 and not message:
                if not self._transcriber:
                    raise HTTPException(501, "Audio transcription not configured")
                try:
                    import base64

                    audio_bytes = base64.b64decode(audio_b64)
                    tr = await self._transcriber.transcribe(
                        audio_bytes, mime=audio_mime or "audio/webm"
                    )
                    message = tr.text
                    log.info("[Audio] Transcribed: %d bytes → '%s'", len(audio_bytes), message[:80])
                except Exception as e:
                    log.error("Transcription failed: %s", e, exc_info=True)
                    print(f"[Audio ERROR] transcription failed: {e}")
                    return ChatResponse(
                        app_id=body.resolved_app_id,
                        app_name=app.name,
                        response=f"Sorry, I couldn't understand the voice message ({type(e).__name__}). Please try again or type your message.",
                        session_id=body.session_id,
                        tools_used=[],
                    )
            if not message:
                raise HTTPException(400, "No message or audio provided")

            if file_context:
                message = f"{file_context}\n\nUser message: {message}"

            session_id = body.session_id or f"{body.resolved_app_id}-{id(body)}"
            if body.thread_id:
                session_id = f"{session_id}-thread-{body.thread_id}"

            # Set per-request user name so _build_effective_message uses it
            _user_token = request_user_name.set(body.sender_name) if body.sender_name else None
            try:
                result = await self.registry.chat(body.resolved_app_id, message, session_id)
            except Exception as e:
                raise HTTPException(500, str(e))
            finally:
                if _user_token is not None:
                    request_user_name.reset(_user_token)

            # Log activity
            self.activity.log(
                actor=body.resolved_app_id,
                actor_name=app.name,
                action="chat",
                detail=result["text"][:120],
                entity_type="response",
                entity_id=session_id,
            )
            for tool_name in result.get("tools_used", []):
                self.activity.log(
                    actor=body.resolved_app_id,
                    actor_name=app.name,
                    action="tool_call",
                    detail=f"Called {tool_name}",
                    entity_type="tool",
                    entity_id=tool_name,
                )

            # Collect file attachments from response text AND tool outputs
            scan_text = result["text"] + "\n" + "\n".join(result.get("tool_outputs", []))
            attachments = _collect_file_attachments(scan_text)
            # The runtime already restored canonical mcp-ui blocks before
            # returning; don't double-restore here (the turn buffer is empty
            # by now and the tool_outputs fallback would re-inject markers).
            response_text = result["text"]
            if attachments:
                # If the agent's text paraphrased ("I attached…") and dropped
                # the markdown link, append a footer so the link is clickable.
                response_text = _ensure_attachments_referenced(response_text, attachments)
                store = getattr(self.registry, "_history_store", None)
                if store and hasattr(store, "update_last_attachments"):
                    try:
                        store.update_last_attachments(
                            session_id, [a.model_dump() for a in attachments]
                        )
                    except Exception as _e:
                        log.warning("update_last_attachments failed: %s", _e)

            return ChatResponse(
                app_id=body.resolved_app_id,
                app_name=app.name,
                response=response_text,
                session_id=session_id,
                tools_used=result.get("tools_used", []),
                attachments=attachments,
            )

        @r.get("/chat/history")
        async def chat_history(app_id: str, session_id: str, limit: int = 50):
            """Get previous messages for an agent DM session."""
            history = self.registry._get_history(session_id)
            messages = []
            for m in history[-limit:]:
                msg = {"role": m["role"], "content": m["content"]}
                if m.get("attachments"):
                    msg["attachments"] = m["attachments"]
                messages.append(msg)
            return {"messages": messages}

        @r.delete("/chat/history")
        async def clear_chat_history(app_id: str, session_id: str):
            """Wipe the in-memory history for a session — the model
            forgets the conversation and the SOUL.md "first message"
            branch fires on the next turn. Used by the "Reset chat"
            button in the workspace chat header. Returns {cleared: bool}
            where False means there was nothing cached (no-op).
            """
            store = getattr(self.registry, "_history_store", None)
            if store is None or not hasattr(store, "clear"):
                return {"cleared": False, "reason": "no_store"}
            cleared = bool(store.clear(session_id))
            return {"cleared": cleared, "session_id": session_id}

        @r.post("/chat/stream")
        async def chat_stream(body: ChatRequest):
            app = self.registry.get(body.resolved_app_id)
            if not app:
                raise HTTPException(404, f"App '{body.resolved_app_id}' not found")

            # Resolve files + audio
            transcribed_text = None
            message = body.message
            file_context, audio_b64, audio_mime = _resolve_files(
                body.file_ids, body.attachments, tenant_id=self.tenant_id
            )

            audio_b64 = audio_b64 or body.media_base64
            audio_mime = audio_mime or body.media_mime
            if audio_b64 and not message:
                if not self._transcriber:
                    raise HTTPException(501, "Audio transcription not configured")
                try:
                    import base64

                    audio_bytes = base64.b64decode(audio_b64)
                    tr = await self._transcriber.transcribe(
                        audio_bytes, mime=audio_mime or "audio/webm"
                    )
                    message = tr.text
                    transcribed_text = message
                    log.info("[Audio] Transcribed: %d bytes → '%s'", len(audio_bytes), message[:80])
                except Exception as e:
                    log.error("Transcription failed in stream: %s", e, exc_info=True)
                    print(f"[Audio ERROR] stream transcription failed: {e}")
                    error_msg = f"Sorry, I could not understand the voice message ({type(e).__name__}). Please try again or type your message."

                    async def error_stream():
                        yield f"data: {json.dumps({'type': 'response', 'text': error_msg})}\n\n"

                    return StreamingResponse(error_stream(), media_type="text/event-stream")
            if not message:
                raise HTTPException(400, "No message or audio provided")

            if file_context:
                message = f"{file_context}\n\nUser message: {message}"

            session_id = body.session_id or f"{body.resolved_app_id}-{id(body)}"
            if body.thread_id:
                session_id = f"{session_id}-thread-{body.thread_id}"

            # Capture sender_name for use inside the async generator
            _req_sender_name = body.sender_name

            # Disconnect-resilient streaming: the run is a background task
            # pumping events into a queue, and the SSE generator only drains
            DONE = object()

            async def _drive_chat(out_queue: asyncio.Queue[Any]):
                """Run the agent loop, push processed events into the queue.
                Always finishes — never cancelled by client disconnect."""
                _user_token = request_user_name.set(_req_sender_name) if _req_sender_name else None
                try:
                    if transcribed_text:
                        await out_queue.put({"type": "transcription", "text": transcribed_text})
                    async for event in self.registry.chat_stream(
                        body.resolved_app_id,
                        message,
                        session_id,
                    ):
                        if event["type"] == "tool_call":
                            self.activity.log(
                                actor=body.resolved_app_id,
                                actor_name=app.name,
                                action="tool_call",
                                detail=f"Called {event['name']}",
                                entity_type="tool",
                                entity_id=event["name"],
                            )
                            await out_queue.put(event)
                            continue
                        elif event["type"] == "response":
                            # Canonical mcp-ui blocks already restored inside
                            # the runtime before this event was emitted.
                            self.activity.log(
                                actor=body.resolved_app_id,
                                actor_name=app.name,
                                action="chat",
                                detail=event["text"][:120],
                                entity_type="response",
                                entity_id=session_id,
                            )
                            scan_text = (
                                event["text"] + "\n" + "\n".join(event.get("tool_outputs", []))
                            )
                            attachments = _collect_file_attachments(scan_text)
                            if attachments:
                                event["text"] = _ensure_attachments_referenced(
                                    event["text"],
                                    attachments,
                                )
                                att_dicts = [a.model_dump() for a in attachments]
                                event["attachments"] = att_dicts
                                store = getattr(self.registry, "_history_store", None)
                                if store and hasattr(store, "update_last_attachments"):
                                    try:
                                        store.update_last_attachments(session_id, att_dicts)
                                    except Exception as _e:
                                        log.warning("update_last_attachments failed: %s", _e)
                            try:
                                from .widget_validator import warn_if_dropped

                                warn_if_dropped(
                                    event["text"],
                                    event.get("tool_outputs"),
                                    agent_id=body.resolved_app_id,
                                )
                            except Exception as _e:
                                log.debug("widget validator skipped: %s", _e)
                            event.pop("tool_outputs", None)
                        await out_queue.put(event)
                except Exception as exc:  # noqa: BLE001
                    # Surface the failure to whichever consumer is still
                    # reading; the inner runtime should have already logged
                    # detail. Putting a typed event lets the client render
                    # an error toast if it's still attached.
                    log.error("chat_stream task failed: %s", exc, exc_info=True)
                    try:
                        await out_queue.put(
                            {
                                "type": "error",
                                "text": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    except Exception:
                        pass
                finally:
                    if _user_token is not None:
                        request_user_name.reset(_user_token)
                    try:
                        await out_queue.put(DONE)
                    except Exception:
                        pass

            async def event_stream():
                # Bounded queue keeps memory in check if the agent emits
                # 1000s of events while the client is gone (it shouldn't,
                # but capping the queue means a runaway loop doesn't OOM
                # the api process).
                queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1024)
                task = asyncio.create_task(_drive_chat(queue))
                # Strong-ref the task on the gateway instance so it isn't
                # GC'd if the SSE generator unwinds early (CPython's
                # well-known asyncio.create_task footgun: weak references
                # only). The done callback removes it once finished.
                self._inflight_chat_tasks.add(task)
                task.add_done_callback(self._inflight_chat_tasks.discard)
                try:
                    while True:
                        ev = await queue.get()
                        if ev is DONE:
                            break
                        yield f"data: {json.dumps(ev)}\n\n"
                        # `await sleep(0)` flushes the previous yield to
                        # the wire before we wait on queue.get() again,
                        # so tool_call indicators appear in the UI as
                        # they happen instead of being held in a buffer.
                        await asyncio.sleep(0)
                finally:
                    # Critical: do NOT cancel the task. If the client is
                    # gone, the agent run still has to complete so its
                    # final assistant message lands in history.
                    if not task.done():
                        # One last detach — task continues running on the
                        # loop. Suppress any task-result warnings.
                        task.add_done_callback(
                            lambda t: t.exception() if not t.cancelled() else None
                        )

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        @r.get("/activity")
        async def get_activity(
            limit: int = 50, actor: str | None = None, action: str | None = None
        ):
            return {"events": self.activity.query(limit=limit, actor=actor, action=action)}

        # ─── Telegram pairings (, multi-bot) ──
        # File-as-truth, one .pairings file per bot. UI lists
        # pending requests + approves/revokes per bot. Single-bot
        # tenants pass `bot=default` (or omit it — list returns all
        # configured bots' pairings keyed by bot name).

        def _bots() -> list[dict]:
            from runspace.ingestion.pairing import resolve_telegram_bots

            cfg = getattr(self, "_workspace_cfg", None) or {}
            return resolve_telegram_bots(cfg)

        def _pairing_state(bot: str | None = None):
            from pathlib import Path as _P

            from runspace.ingestion.pairing import FilePairingState

            base = (getattr(self, "_workspace_cfg", None) or {}).get("_base_dir")
            if not base:
                raise HTTPException(503, "Pairing store not configured for this tenant")
            bots = _bots()
            if not bots:
                raise HTTPException(503, "No Telegram bots configured")
            target = bots[0]
            if bot:
                match = next((b for b in bots if b.get("name") == bot), None)
                if not match:
                    raise HTTPException(
                        404, f"unknown bot {bot!r}; available: {[b.get('name') for b in bots]}"
                    )
                target = match
            filename = target.get("pairing_filename") or ".pairings.json"
            return FilePairingState(_P(base) / filename), target.get("name")

        # Auth on sensitive pairing routes. List is read-only and
        # exposes only handle/sender_id (no PII), so it stays open;
        # approve/revoke modify trust state and require admin.
        admin_deps = [self._admin_dependency] if self._admin_dependency else []

        @r.get("/pairings")
        async def list_pairings(bot: str | None = None):
            """List pending + approved pairings.

            With `?bot=<name>` returns just that bot's. Without, returns
            ALL configured bots' pairings keyed by bot name — the UI's
            tab/dropdown reads this shape directly.
            """
            try:
                bots = _bots()
            except HTTPException:
                return {"pending": [], "approved": []}
            if not bots:
                return {"pending": [], "approved": []}

            # Filter to one bot if requested.
            if bot:
                target_bot = next((b for b in bots if b.get("name") == bot), None)
                if not target_bot:
                    raise HTTPException(404, f"unknown bot {bot!r}")
                bots = [target_bot]

            from pathlib import Path as _P

            from runspace.ingestion.pairing import FilePairingState

            base = (getattr(self, "_workspace_cfg", None) or {}).get("_base_dir")
            if not base:
                return {"pending": [], "approved": []}
            out: dict = {}
            for b in bots:
                fname = b.get("pairing_filename") or ".pairings.json"
                state = FilePairingState(_P(base) / fname)
                out[b.get("name") or "default"] = {
                    "pending": state.list_pending(),
                    "approved": state.list_approved(),
                }
            # Single-bot back-compat: when only one bot exists, return
            # the flat shape the existing UI panel expects (so we don't
            # break the deployed Pairings panel before it learns about
            # the multi-bot shape).
            if len(out) == 1:
                only = next(iter(out.values()))
                return {**only, "bots": list(out.keys())}
            return {"by_bot": out, "bots": list(out.keys())}

        @r.post("/pairings/{code}/approve", dependencies=admin_deps)
        async def approve_pairing(code: str, bot: str | None = None):
            store, bot_name = _pairing_state(bot)
            entry = store.approve(code)
            if not entry:
                raise HTTPException(404, f"pairing code {code!r} not found or expired")
            self.activity.log(
                actor="owner",
                actor_name=self._user_name,
                action="pairing_approved",
                detail=f"[{bot_name}] @{entry.get('sender_handle', '?')} ({entry.get('sender_id')})",
                entity_type="pairing",
                entity_id=entry["sender_id"],
            )
            return {"ok": True, "bot": bot_name, **entry}

        @r.post("/pairings/{sender_id}/revoke", dependencies=admin_deps)
        async def revoke_pairing(sender_id: str, bot: str | None = None):
            store, bot_name = _pairing_state(bot)
            ok = store.revoke(sender_id)
            if not ok:
                raise HTTPException(404, f"sender {sender_id!r} was not approved")
            self.activity.log(
                actor="owner",
                actor_name=self._user_name,
                action="pairing_revoked",
                detail=f"[{bot_name}] sender_id={sender_id}",
                entity_type="pairing",
                entity_id=sender_id,
            )
            return {"ok": True, "bot": bot_name, "sender_id": sender_id}

        # ─── Discovered (unbound) chats + binding-add ( hot reload) ──
        # When the bot is added to a group / channel without an

        @r.get("/discovered-chats")
        async def list_discovered_chats(bot: str | None = None):
            from pathlib import Path as _P

            from runspace.ingestion.pairing import resolve_telegram_bots

            cfg = getattr(self, "_workspace_cfg", None) or {}
            base = cfg.get("_base_dir")
            if not base:
                return {"by_bot": {}}
            bots = resolve_telegram_bots(cfg)
            if bot:
                bots = [b for b in bots if b.get("name") == bot]
            out: dict = {}
            for b in bots:
                fname = f".discovered-chats-{b.get('name')}.json"
                p = _P(base) / fname
                try:
                    chats = json.loads(p.read_text()) if p.exists() else {}
                except Exception:
                    chats = {}
                # Filter out chats that already have a binding so the
                # UI only highlights chats genuinely awaiting one.
                bound_chat_ids = {
                    str(eb.get("chat_id"))
                    for eb in (cfg.get("external_channels") or [])
                    if eb.get("provider") == "telegram"
                    and (not eb.get("bot") or eb.get("bot") == b.get("name"))
                }
                pending = [
                    {"chat_id": cid, **rec}
                    for cid, rec in chats.items()
                    if cid not in bound_chat_ids
                ]
                pending.sort(key=lambda r: r.get("last_seen", ""), reverse=True)
                out[b.get("name") or "default"] = pending
            return {"by_bot": out, "bots": list(out.keys())}

        @r.get("/external-channels")
        async def list_external_channels():
            """Return current `external_channels` bindings, enriched
            with the chat title pulled from the discovery file when
            available (so the UI shows "Wine Habits" instead of a
            raw chat_id like `-1003862790454`).
            """
            from pathlib import Path as _P

            cfg = getattr(self, "_workspace_cfg", None) or {}
            base = cfg.get("_base_dir")
            bindings = list(cfg.get("external_channels") or [])

            # Build a lookup of discovery metadata per (bot, chat_id)
            # so we can enrich each binding with its human-readable
            # title.
            from runspace.ingestion.pairing import resolve_telegram_bots

            disc: dict[tuple[str, str], dict] = {}
            if base:
                for bot in resolve_telegram_bots(cfg):
                    name = bot.get("name") or "default"
                    p = _P(base) / f".discovered-chats-{name}.json"
                    if not p.exists():
                        continue
                    try:
                        for cid, rec in (json.loads(p.read_text()) or {}).items():
                            disc[(name, str(cid))] = rec
                    except Exception:
                        continue

            enriched: list[dict] = []
            for b in bindings:
                if b.get("provider") != "telegram":
                    enriched.append(dict(b))
                    continue
                bot_name = b.get("bot") or ""
                cid = str(b.get("chat_id"))
                rec = disc.get((bot_name, cid))
                e = dict(b)
                if rec:
                    if rec.get("title"):
                        e["title"] = rec["title"]
                    if rec.get("type"):
                        e["chat_type"] = rec["type"]
                enriched.append(e)
            return {"bindings": enriched}

        @r.delete("/external-channels/{binding_id}", dependencies=admin_deps)
        async def remove_external_channel(binding_id: str):
            """Remove an external_channels entry by `id` and hot-
            reload the cfg in place. Atomic-rename write to
            workspace.yml; activity-logged for audit. Returns 404 if
            no binding with that id exists.
            """
            from pathlib import Path as _P

            import yaml as _yaml

            cfg = getattr(self, "_workspace_cfg", None)
            path = getattr(self, "_workspace_cfg_path", None)
            if cfg is None or not path:
                raise HTTPException(
                    503, "workspace.yml path not stashed; this gateway can't hot-reload"
                )
            with open(path) as f:
                disk = _yaml.safe_load(f) or {}
            bindings = list(disk.get("external_channels") or [])
            kept = [b for b in bindings if str(b.get("id")) != binding_id]
            if len(kept) == len(bindings):
                raise HTTPException(404, f"binding {binding_id!r} not found")
            disk["external_channels"] = kept
            tmp = _P(str(path) + ".tmp")
            tmp.write_text(
                _yaml.safe_dump(
                    disk,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            )
            os.replace(tmp, path)
            diff = self.reload_config()
            self.activity.log(
                actor="owner",
                actor_name=self._user_name,
                action="external_channel_unbound",
                detail=f"binding_id={binding_id}",
                entity_type="external_channel",
                entity_id=binding_id,
            )
            return {"ok": True, "removed": binding_id, "reload": diff}

        # ─── Telegram bots: configure dmAgent / dmPolicy from UI ─────
        # Read-side surfaces the per-tenant bot list with current

        @r.get("/telegram-bots")
        async def list_telegram_bots():
            from runspace.ingestion.pairing import resolve_telegram_bots
            from runspace.ingestion.telegram import _bot_token

            cfg = getattr(self, "_workspace_cfg", None) or {}
            bots = resolve_telegram_bots(cfg)
            out = []
            for b in bots:
                token = _bot_token(cfg, b)
                out.append(
                    {
                        "name": b.get("name"),
                        "transport": b.get("transport") or "webhook",
                        "dmAgent": b.get("dmAgent"),
                        "dmPolicy": b.get("dmPolicy") or "pairing",
                        "allowFrom": b.get("allowFrom") or [],
                        "has_token": bool(token),
                        "token_ref": b.get("token") or "",  # `${ENV_VAR}` ref
                    }
                )
            return {"bots": out}

        @r.post("/telegram-bots", dependencies=admin_deps)
        async def add_telegram_bot(body: dict):
            """Append a new entry to `messaging.telegram_bots` and
            hot-reload the cfg in place. Lifespan picks up the new
            bot and starts its polling task without a restart.

            Body:
              {
                "name":     "max",                       # required
                "token_ref":"${ACME_TELEGRAM_BOT_TOKEN_MAX}",
                "dmAgent":  "booking",
                "dmPolicy": "open",        # default "pairing"
                "transport":"polling",     # default "polling"
              }

            The actual token is NEVER taken from the body — only the
            `${ENV_VAR}` reference. Operator must add the env var to
            runtime.env on the host first; the modal in the UI
            tells them this. Validates: name unique, name slug-shape,
            dmAgent in registered apps when supplied.
            """
            import re as _re
            from pathlib import Path as _P

            import yaml as _yaml

            cfg = getattr(self, "_workspace_cfg", None)
            path = getattr(self, "_workspace_cfg_path", None)
            if cfg is None or not path:
                raise HTTPException(503, "workspace.yml path not stashed")
            name = (body.get("name") or "").strip()
            if not name or not _re.fullmatch(r"[a-z0-9_-]+", name):
                raise HTTPException(
                    400, f"name must be lowercase alphanumeric (with - or _); got {name!r}"
                )
            token_ref = (body.get("token_ref") or "").strip()
            if not token_ref:
                raise HTTPException(400, "token_ref required (e.g. ${ACME_TELEGRAM_BOT_TOKEN_X})")
            dm_policy = (body.get("dmPolicy") or "pairing").strip().lower()
            if dm_policy not in ("pairing", "allowlist", "open", "disabled"):
                raise HTTPException(400, f"invalid dmPolicy {dm_policy!r}")
            transport = (body.get("transport") or "polling").strip().lower()
            if transport not in ("polling", "webhook"):
                raise HTTPException(400, f"invalid transport {transport!r}")

            with open(path) as f:
                disk = _yaml.safe_load(f) or {}
            disk.setdefault("messaging", {}).setdefault("telegram_bots", [])
            existing_names = {
                (b or {}).get("name") for b in disk["messaging"]["telegram_bots"] or []
            }
            if name in existing_names:
                raise HTTPException(409, f"bot {name!r} already exists")
            new_bot = {
                "name": name,
                "token": token_ref,
                "transport": transport,
                "dmPolicy": dm_policy,
            }
            if body.get("dmAgent"):
                new_bot["dmAgent"] = body["dmAgent"]
            if body.get("allowFrom"):
                new_bot["allowFrom"] = list(body["allowFrom"])
            disk["messaging"]["telegram_bots"].append(new_bot)
            tmp = _P(str(path) + ".tmp")
            tmp.write_text(
                _yaml.safe_dump(
                    disk,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            )
            os.replace(tmp, path)
            diff = self.reload_config()
            await self.start_external_channel_transports()
            self.activity.log(
                actor="owner",
                actor_name=self._user_name,
                action="telegram_bot_added",
                detail=f"name={name} dmAgent={body.get('dmAgent')} "
                f"policy={dm_policy} transport={transport}",
                entity_type="telegram_bot",
                entity_id=name,
            )
            return {"ok": True, "bot": new_bot, "reload": diff}

        @r.delete("/telegram-bots/{bot_name}", dependencies=admin_deps)
        async def remove_telegram_bot(bot_name: str):
            """Remove a bot entry from `messaging.telegram_bots`.
            Stops its polling task. Pairing + offset state files are
            left on disk so re-adding the same name later restores
            approvals.
            """
            from pathlib import Path as _P

            import yaml as _yaml

            cfg = getattr(self, "_workspace_cfg", None)
            path = getattr(self, "_workspace_cfg_path", None)
            if cfg is None or not path:
                raise HTTPException(503, "workspace.yml path not stashed")
            with open(path) as f:
                disk = _yaml.safe_load(f) or {}
            bots = (disk.get("messaging") or {}).get("telegram_bots") or []
            kept = [b for b in bots if (b or {}).get("name") != bot_name]
            if len(kept) == len(bots):
                raise HTTPException(404, f"bot {bot_name!r} not found")
            disk["messaging"]["telegram_bots"] = kept
            tmp = _P(str(path) + ".tmp")
            tmp.write_text(
                _yaml.safe_dump(
                    disk,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            )
            os.replace(tmp, path)
            # Stop the transport for this bot specifically — the rest
            # keep running.
            for t in list(getattr(self, "_external_transports", []) or []):
                if getattr(t, "_bot_name", None) == bot_name:
                    try:
                        await t.stop()
                    except Exception as e:
                        log.warning("stop bot %s transport failed: %s", bot_name, e)
                    self._external_transports.remove(t)
                    break
            diff = self.reload_config()
            self.activity.log(
                actor="owner",
                actor_name=self._user_name,
                action="telegram_bot_removed",
                detail=f"name={bot_name}",
                entity_type="telegram_bot",
                entity_id=bot_name,
            )
            return {"ok": True, "removed": bot_name, "reload": diff}

        @r.patch("/telegram-bots/{bot_name}", dependencies=admin_deps)
        async def patch_telegram_bot(bot_name: str, body: dict):
            """Update dmAgent / dmPolicy / allowFrom for an existing
            bot. Atomic-rename on workspace.yml + hot-reload. Token
            and transport are NOT editable here — token is sensitive
            (env var managed by host), transport requires a process
            restart to swap polling↔webhook cleanly.
            """
            from pathlib import Path as _P

            import yaml as _yaml

            cfg = getattr(self, "_workspace_cfg", None)
            path = getattr(self, "_workspace_cfg_path", None)
            if cfg is None or not path:
                raise HTTPException(503, "workspace.yml path not stashed")
            # Load disk copy, find bot, update
            with open(path) as f:
                disk = _yaml.safe_load(f) or {}
            msg = disk.get("messaging") or {}
            raw_bots = msg.get("telegram_bots") or []
            target_idx = None
            for i, b in enumerate(raw_bots):
                if isinstance(b, dict) and b.get("name") == bot_name:
                    target_idx = i
                    break
            if target_idx is None:
                raise HTTPException(404, f"unknown bot {bot_name!r}")
            target = raw_bots[target_idx]
            updates: dict = {}
            if "dmAgent" in body:
                target["dmAgent"] = body["dmAgent"] or None
                updates["dmAgent"] = target["dmAgent"]
            if "dmPolicy" in body:
                p = (body["dmPolicy"] or "").strip().lower()
                if p not in ("pairing", "allowlist", "open", "disabled"):
                    raise HTTPException(
                        400,
                        f"invalid dmPolicy {body['dmPolicy']!r}; "
                        f"must be pairing|allowlist|open|disabled",
                    )
                target["dmPolicy"] = p
                updates["dmPolicy"] = p
            if "allowFrom" in body:
                af = body["allowFrom"]
                if not isinstance(af, list):
                    raise HTTPException(400, "allowFrom must be a list")
                target["allowFrom"] = [str(x) for x in af if x]
                updates["allowFrom"] = target["allowFrom"]
            disk["messaging"]["telegram_bots"][target_idx] = target
            tmp = _P(str(path) + ".tmp")
            tmp.write_text(
                _yaml.safe_dump(
                    disk,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            )
            os.replace(tmp, path)
            diff = self.reload_config()
            self.activity.log(
                actor="owner",
                actor_name=self._user_name,
                action="telegram_bot_updated",
                detail=f"[{bot_name}] {updates}",
                entity_type="telegram_bot",
                entity_id=bot_name,
            )
            return {"ok": True, "bot": bot_name, "updates": updates, "reload": diff}

        @r.delete("/discovered-chats/{bot_name}/{chat_id}", dependencies=admin_deps)
        async def reject_discovered_chat(bot_name: str, chat_id: str, leave: bool = True):
            """Reject a discovered chat. Removes it from the
            `.discovered-chats-<bot>.json` file AND (by default) makes
            the bot leave the group on Telegram so it stops receiving
            new messages from there.

            Pass `?leave=false` to only forget the discovery record
            (the bot stays in the group; any future message will
            re-discover it).
            """
            from pathlib import Path as _P

            from runspace.ingestion.pairing import resolve_telegram_bots
            from runspace.ingestion.telegram import (
                _bot_token,
                _leave_chat,
            )

            cfg = getattr(self, "_workspace_cfg", None) or {}
            base = cfg.get("_base_dir")
            if not base:
                raise HTTPException(503, "no _base_dir on workspace_cfg")
            bots = {b.get("name"): b for b in resolve_telegram_bots(cfg)}
            bot_cfg = bots.get(bot_name)
            if not bot_cfg:
                raise HTTPException(404, f"unknown bot {bot_name!r}")

            # 1. Drop the discovery record
            disc_path = _P(base) / f".discovered-chats-{bot_name}.json"
            removed_from_discovery = False
            if disc_path.exists():
                try:
                    data = json.loads(disc_path.read_text())
                except Exception:
                    data = {}
                if isinstance(data, dict) and chat_id in data:
                    del data[chat_id]
                    tmp = disc_path.with_suffix(disc_path.suffix + ".tmp")
                    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
                    os.replace(tmp, disc_path)
                    removed_from_discovery = True

            # 2. Leave the group on Telegram (default behaviour).
            left = False
            if leave:
                token = _bot_token(cfg, bot_cfg)
                if token:
                    try:
                        # Telegram chat_ids can be negative ints; pass
                        # as int when possible to match the format
                        # leaveChat expects.
                        try:
                            cid_param: int | str = int(chat_id)
                        except (TypeError, ValueError):
                            cid_param = chat_id
                        left = await _leave_chat(token, cid_param)
                    except Exception as e:
                        log.warning("[telegram] leaveChat failed for chat %s: %s", chat_id, e)

            self.activity.log(
                actor="owner",
                actor_name=self._user_name,
                action="discovered_chat_rejected",
                detail=f"[{bot_name}] chat={chat_id} leave={leave} left={left}",
                entity_type="discovered_chat",
                entity_id=chat_id,
            )
            return {
                "ok": True,
                "bot": bot_name,
                "chat_id": chat_id,
                "removed_from_discovery": removed_from_discovery,
                "left_group": left,
            }

        @r.post("/external-channels", dependencies=admin_deps)
        async def add_external_channel(body: dict):
            """Append an external_channels binding to workspace.yml
            and hot-reload the cfg in place — no API restart.

            Body:
              {
                "id":      "<binding-id>",            # required
                "chat_id": "-100...",                  # required
                "agent":   "accountant",               # required
                "bot":     "ada",                     # required for multi-bot
                "trusted_senders": ["123"]             # optional
              }
            """
            from pathlib import Path as _P

            import yaml as _yaml

            cfg = getattr(self, "_workspace_cfg", None)
            path = getattr(self, "_workspace_cfg_path", None)
            if cfg is None or not path:
                raise HTTPException(
                    503, "workspace.yml path not stashed; this gateway can't hot-reload"
                )
            for required in ("chat_id", "agent", "bot"):
                if not body.get(required):
                    raise HTTPException(400, f"missing required field: {required}")
            from runspace.ingestion.pairing import resolve_telegram_bots

            bot_names = [b.get("name") for b in resolve_telegram_bots(cfg)]
            if body["bot"] not in bot_names:
                raise HTTPException(400, f"unknown bot {body['bot']!r}; available: {bot_names}")
            new_binding = {
                "provider": "telegram",
                "id": body.get("id") or f"{body['bot']}-{body['chat_id']}",
                "chat_id": body["chat_id"],
                "agent": body["agent"],
                "bot": body["bot"],
            }
            if body.get("trusted_senders"):
                new_binding["trusted_senders"] = body["trusted_senders"]
            # Read+update+atomic-rename. No flock; this is the only
            # writer of workspace.yml at runtime today.
            with open(path) as f:
                disk = _yaml.safe_load(f) or {}
            disk.setdefault("external_channels", [])
            # Reject duplicate (bot, chat_id) — the user just discovered
            # it, no point binding twice.
            for eb in disk["external_channels"]:
                if (
                    eb.get("provider") == "telegram"
                    and str(eb.get("chat_id")) == str(body["chat_id"])
                    and (eb.get("bot") or "") == body["bot"]
                ):
                    raise HTTPException(
                        409, f"chat {body['chat_id']!r} already bound for bot {body['bot']!r}"
                    )
            disk["external_channels"].append(new_binding)
            tmp = _P(str(path) + ".tmp")
            tmp.write_text(
                _yaml.safe_dump(
                    disk,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            )
            os.replace(tmp, path)
            diff = self.reload_config()
            self.activity.log(
                actor="owner",
                actor_name=self._user_name,
                action="external_channel_bound",
                detail=f"[{body['bot']}] chat={body['chat_id']} → {body['agent']}",
                entity_type="external_channel",
                entity_id=str(body["chat_id"]),
            )
            return {"ok": True, "binding": new_binding, "reload": diff}

        @r.post("/reload-config", dependencies=admin_deps)
        async def hot_reload_config():
            """Re-read workspace.yml and apply changes in place.

            Bindings + dmAgent / dmPolicy / allowFrom: instant.
            New bots in `messaging.telegram_bots`: their polling task
            starts here too (existing bots untouched).
            """
            diff = self.reload_config()
            await self.start_external_channel_transports()
            return {"ok": True, "reload": diff}

        @r.post("/agents/{agent_id}/reload", dependencies=admin_deps)
        async def hot_reload_agent(agent_id: str):
            """Drop the cached agentino Agent for this agent so the
            next chat() call rebuilds with the current SOUL.md +
            tool code from disk. Closes the SOUL-iteration loop
            without a container restart.

            Note: agent metadata (name, role, avatar, tool_dir
            paths) comes from workspace.yml — to reload those, hit
            /api/workspace/reload-config first.
            """
            if not self.registry.get(agent_id):
                raise HTTPException(404, f"unknown agent {agent_id!r}")
            had_agent = self.registry.reload_agent(agent_id)
            self.activity.log(
                actor="owner",
                actor_name=self._user_name,
                action="agent_reloaded",
                detail=f"{agent_id} (had_cached_agent={had_agent})",
                entity_type="agent",
                entity_id=agent_id,
            )
            return {"ok": True, "agent_id": agent_id, "had_cached_agent": had_agent}

        # ─── Routine CRUD (file-as-truth) ──────────────────
        # Source of truth = `tenants/<id>/routines.yml` on disk; runtime

        def _persona_for_agent(agent_id: str) -> dict:
            """Look up sender_name / avatar / color for an agent so the
            routine post matches the agent's persona in the workspace."""
            app = self.registry.apps.get(agent_id) if hasattr(self.registry, "apps") else None
            if not app:
                return {}
            return {
                "sender_name": getattr(app, "name", agent_id.capitalize()),
                "sender_avatar": getattr(app, "avatar", "🤖"),
                "sender_color": getattr(app, "color", "#6B7280"),
            }

        def _job_to_ui(job) -> dict:
            d = job.payload.data or {}
            delivery = d.get("delivery") or {"kind": "silent", "target": None}
            return {
                "id": job.name,
                "schedule": job.schedule.cron_expr or "",
                "prompt": d.get("prompt", ""),
                "enabled": bool(job.enabled),
                "next_run": job.next_run_at.isoformat() if job.next_run_at else None,
                "source": "file",
                "delivery": delivery,
                "metadata": {
                    "agent_id": d.get("agent_id", ""),
                    "agent_name": d.get("sender_name") or d.get("agent_id", "").capitalize(),
                    "description": d.get("description", job.name),
                },
            }

        @r.get("/routines")
        async def list_routines():
            """All routines for the tenant — read from routines.yml +
            sibling state json. No DB calls."""
            if not self.routines_store:
                return {"routines": []}
            return {"routines": [_job_to_ui(j) for j in self.routines_store.list()]}

        @r.post("/routines")
        async def create_routine(body: RoutineCreateRequest):
            """Create a routine. agent_id, schedule (cron expr), prompt
            are required. Writes a new entry to `routines.yml` — picked
            up by the next cron tick (≤60s)."""
            if not self.routines_store:
                raise HTTPException(503, "Routines store not configured for this tenant")
            if body.agent_id not in (self.registry.apps if hasattr(self.registry, "apps") else {}):
                raise HTTPException(400, f"Unknown agent: {body.agent_id}")

            # Generate stable id from description (kebab-case) +
            # fall back to agent + timestamp for nameless routines.
            import re as _re
            import time as _time

            base_label = body.description or f"{body.agent_id}-routine"
            name = _re.sub(r"[^a-z0-9-]+", "-", base_label.lower()).strip("-")
            if not name:
                name = f"{body.agent_id}-{int(_time.time())}"

            delivery = {"kind": body.delivery.kind, "target": body.delivery.target}
            if delivery["kind"] in ("channel", "dm") and not delivery["target"]:
                raise HTTPException(400, f"delivery.target required for kind={delivery['kind']!r}")

            persona = _persona_for_agent(body.agent_id)

            from runspace.contracts.scheduling import CronJob, Payload, Schedule, ScheduleKind

            job = CronJob(
                id=name,
                tenant_id=self.tenant_id or "",
                name=name,
                schedule=Schedule(kind=ScheduleKind.CRON, cron_expr=body.schedule),
                payload=Payload(
                    kind="routine",
                    data={
                        "agent_id": body.agent_id,
                        "prompt": body.prompt,
                        "description": body.description or name,
                        "delivery": delivery,
                        "sender_name": persona.get("sender_name"),
                        "sender_avatar": persona.get("sender_avatar"),
                        "sender_color": persona.get("sender_color"),
                    },
                ),
                enabled=True,
            )
            try:
                self.routines_store.upsert(job)
            except Exception as e:
                log.exception("[routines] create failed")
                raise HTTPException(500, f"create failed: {e}")

            return {
                "id": name,
                "agent_id": body.agent_id,
                "schedule": body.schedule,
                "enabled": True,
                "next_run": job.next_run_at.isoformat() if job.next_run_at else None,
            }

        @r.patch("/routines/{routine_id}")
        async def patch_routine(routine_id: str, body: dict):
            """Update enabled/prompt/schedule/delivery on an existing
            routine. Read-modify-upsert through the file store."""
            if not self.routines_store:
                raise HTTPException(503, "Routines store not configured for this tenant")
            jobs = {j.name: j for j in self.routines_store.list()}
            job = jobs.get(routine_id)
            if not job:
                raise HTTPException(404, f"routine {routine_id!r} not found")

            updated_fields: list[str] = []
            if "enabled" in body:
                job.enabled = bool(body["enabled"])
                updated_fields.append("enabled")
            if "schedule" in body:
                from runspace.contracts.scheduling import Schedule, ScheduleKind

                job.schedule = Schedule(kind=ScheduleKind.CRON, cron_expr=body["schedule"])
                # Recompute next_run_at against the new expression so
                # the UI reflects the change immediately.
                job.next_run_at = job.schedule.next_run()
                updated_fields.append("schedule")
            if "prompt" in body:
                job.payload.data["prompt"] = body["prompt"]
                updated_fields.append("prompt")
            if "delivery" in body:
                d = body["delivery"] or {}
                if d.get("kind") not in ("channel", "dm", "silent"):
                    raise HTTPException(400, "delivery.kind must be channel|dm|silent")
                job.payload.data["delivery"] = {
                    "kind": d["kind"],
                    "target": d.get("target"),
                }
                updated_fields.append("delivery")

            if not updated_fields:
                return {"ok": True, "noop": True}

            self.routines_store.upsert(job)
            return {"ok": True, "id": routine_id, "updated_fields": updated_fields}

        @r.delete("/routines/{routine_id}")
        async def delete_routine(routine_id: str):
            if not self.routines_store:
                raise HTTPException(503, "Routines store not configured for this tenant")
            jobs = {j.name for j in self.routines_store.list()}
            if routine_id not in jobs:
                raise HTTPException(404, f"routine {routine_id!r} not found")
            self.routines_store.delete(routine_id)
            return {"ok": True, "deleted": routine_id}

        @r.post("/routines/{routine_id}/run")
        async def run_routine(routine_id: str, live: bool = False):
            """Manual trigger. Same code path the cron tick uses — chat
            the agent, optionally post the reply.

            Args:
              live: when True, also post the agent's reply to the
                    routine's configured channel (same effect as the
                    cron tick). When False (default), this is a
                    preview — returns the agent's text without
                    posting anywhere.
            """
            if not self.routines_store:
                raise HTTPException(503, "Routines store not configured for this tenant")
            jobs = {j.name: j for j in self.routines_store.list()}
            job = jobs.get(routine_id)
            if not job:
                raise HTTPException(404, f"Routine '{routine_id}' not found")

            pd = job.payload.data or {}
            agent_id = pd.get("agent_id")
            prompt = pd.get("prompt") or ""
            if not agent_id:
                raise HTTPException(500, f"routine {routine_id!r} missing agent_id")

            result = await self.registry.chat(
                agent_id,
                prompt,
                f"routine:{self.tenant_id}:{routine_id}",
            )
            text = (result or {}).get("text", "").strip()
            delivery = pd.get("delivery") or {"kind": "silent", "target": None}
            posted = False
            if live and text and delivery["kind"] != "silent":
                try:
                    # The gateway already resolved a messaging backend at
                    # construction — Supabase when configured, SQLite
                    messaging = _require_messaging()
                    channel = None
                    if delivery["kind"] == "channel":
                        channel = messaging.get_channel_by_slug(delivery.get("target") or "")
                    elif delivery["kind"] == "dm":
                        target = delivery.get("target") or ""
                        channel = (
                            messaging.get_or_create_dm_channel(target)
                            if hasattr(messaging, "get_or_create_dm_channel")
                            else messaging.get_channel_by_slug(f"dm-{target}")
                        )
                    if channel:
                        messaging.send_message(
                            channel_id=channel["id"],
                            sender_type="agent",
                            sender_id=agent_id,
                            sender_name=pd.get("sender_name") or agent_id.capitalize(),
                            sender_avatar=pd.get("sender_avatar") or "🤖",
                            sender_color=pd.get("sender_color") or "#6B7280",
                            content=text,
                            metadata={
                                "kind": "routine",
                                "routine_name": routine_id,
                                "manual_run": True,
                                "tools_used": result.get("tools_used", []),
                            },
                        )
                        posted = True
                except Exception as e:
                    log.warning("[routines] live post failed: %s", e)

            self.activity.log(
                actor=agent_id,
                actor_name=pd.get("sender_name") or agent_id.capitalize(),
                action="routine_run" if not live else "routine_run_live",
                detail=f"{routine_id}: {(result.get('text') or '')[:100]}",
                entity_type="routine",
                entity_id=routine_id,
            )
            return {
                "routine_id": routine_id,
                "agent_id": agent_id,
                "text": result.get("text", ""),
                "tools_used": result.get("tools_used", []),
                "live": live,
                "posted": posted,
                "delivery": delivery if live else None,
            }

        @r.get("/files/{file_id}")
        async def serve_file(file_id: str):
            """Serve a stored file (uploaded scan or generated PDF) for
            download. Reads via protocols.FileStorage — Supabase or
            local depending on which is configured.

            Tenant scoping comes from the gateway instance — this gateway
            is bound to one tenant, so files are looked up under that
            tenant's namespace. Cross-tenant access is impossible by
            construction.
            """
            from runspace.protocols import get_file_storage

            storage = get_file_storage()
            tenant_id = self.tenant_id or "default"
            try:
                meta = storage.metadata(tenant_id, file_id)
                content = storage.get(tenant_id, file_id)
            except (FileNotFoundError, ValueError):
                raise HTTPException(404, "File not found")
            from fastapi.responses import Response

            return Response(
                content=content,
                media_type=meta.content_type,
                headers={"Content-Disposition": f'attachment; filename="{meta.original_name}"'},
            )

        @r.post("/upload")
        async def upload_file(file: UploadFile = File(...)):
            """Upload a file (audio, document, image). Returns file_id for use in chat requests.

            Routes through protocols.FileStorage — auto-detected to use
            Supabase Storage if configured, otherwise local-disk. Tenant
            scoping is enforced inside the storage adapter.
            """
            data = await file.read()
            if not data:
                raise HTTPException(400, "Empty file")
            from runspace.protocols import get_file_storage

            storage = get_file_storage()
            tenant_id = self.tenant_id or "default"
            meta = storage.put(
                tenant_id,
                file.filename or "upload",
                data,
                content_type=file.content_type or "application/octet-stream",
            )
            log.info(
                "[Upload] %s → %s (%d bytes, tenant=%s, backend=%s)",
                file.filename,
                meta.file_id,
                meta.size_bytes,
                tenant_id,
                type(storage).__name__,
            )

            # Bridge to the platform's `documents` table so chat-uploaded
            # files also appear on the Documents page (otherwise they
            try:
                self._mirror_to_documents_table(
                    tenant_id=tenant_id,
                    file_id=meta.file_id,
                    original_name=meta.original_name,
                    size_bytes=meta.size_bytes,
                    content_type=meta.content_type or "application/octet-stream",
                )
            except Exception as e:
                log.warning("[Upload] documents mirror failed for %s: %s", meta.file_id, e)

            return {
                "file_id": meta.file_id,
                "name": meta.original_name,
                "size": meta.size_bytes,
                "type": meta.content_type,
            }

        # ---- Messaging routes (channels + messages) -----------------------

        def _require_messaging():
            if not self._messaging:
                raise HTTPException(503, "Messaging not configured (SUPABASE_URL/KEY missing)")
            return self._messaging

        @r.get("/users")
        async def list_workspace_users():
            """List humans + agents that can be `@`-mentioned in a chat composer.

            Returns a unified list with `kind = "user" | "agent"` so the frontend
            can show both in the autocomplete (with separate sections / badges).

            Tenant-scoped: only the current gateway tenant's people.
            """
            # Agents come from the in-process registry — already filtered to this tenant.
            agents_out = [
                {
                    "kind": "agent",
                    "id": a["id"],
                    "name": a["name"],
                    "avatar": a.get("avatar", "🤖"),
                    "color": a.get("color", "#6B7280"),
                    "role": a.get("role", ""),
                }
                for a in self.registry.list_apps()
            ]

            users_out: list[dict] = []
            if self.tenant_id:
                try:
                    import os as _os

                    from supabase import create_client

                    url = _os.environ.get("SUPABASE_URL")
                    key = _os.environ.get("SUPABASE_KEY")
                    if url and key:
                        client = create_client(url, key)
                        # Members of this tenant.
                        membership = (
                            client.table("tenant_users")
                            .select("user_id,role")
                            .eq("tenant_id", self.tenant_id)
                            .execute()
                        )
                        rows = membership.data or []
                        emails: dict = {}
                        try:
                            # Resolve emails via auth admin API. Best-effort —
                            # fall back to user_id if anything fails (e.g.
                            # service role can't list_users in some setups).
                            auth_users = client.auth.admin.list_users()
                            emails = {u.id: u.email for u in auth_users}
                        except Exception:
                            pass
                        for row in rows:
                            uid = row.get("user_id", "")
                            email = emails.get(uid, "")
                            display_name = (email.split("@")[0] if email else uid) or "user"
                            users_out.append(
                                {
                                    "kind": "user",
                                    "id": uid,
                                    "name": display_name,
                                    "email": email,
                                    "role": row.get("role", "member"),
                                    "avatar": "👤",
                                    "color": "#6B7280",
                                }
                            )
                except Exception as e:
                    log.warning("workspace /users — could not resolve users: %s", e)

            return {"agents": agents_out, "users": users_out}

        @r.get("/channels")
        async def list_channels():
            svc = _require_messaging()
            return {"channels": svc.list_channels()}

        @r.post("/channels")
        async def create_channel(body: dict):
            svc = _require_messaging()
            name = body.get("name", "")
            slug = body.get("slug", "")
            if not name or not slug:
                raise HTTPException(400, "name and slug required")
            channel = svc.create_channel(
                name=name,
                slug=slug,
                is_private=body.get("is_private", False),
                created_by=body.get("created_by", ""),
                description=body.get("description", ""),
                icon=body.get("icon", "Hash"),
            )
            return channel

        @r.get("/channels/{slug}/messages")
        async def get_channel_messages(slug: str, limit: int = 50, before: str | None = None):
            svc = _require_messaging()
            channel = svc.get_channel_by_slug(slug)
            if not channel:
                raise HTTPException(404, f"Channel '{slug}' not found")
            messages = svc.get_channel_messages(channel["id"], limit=limit, before=before)
            return {"messages": messages, "channel": channel}

        @r.post("/channels/{slug}/messages")
        async def send_channel_message(slug: str, body: dict):
            svc = _require_messaging()
            channel = svc.get_channel_by_slug(slug)
            if not channel:
                raise HTTPException(404, f"Channel '{slug}' not found")

            content = body.get("content", "")
            if not content:
                raise HTTPException(400, "content required")

            sender_name = body.get("sender_name", self._user_name)
            sender_id = body.get("sender_id", "user")
            # Respect the body's sender_type. The frontend uses this same
            # endpoint to persist BOTH user messages and bot replies it
            raw_sender_type = body.get("sender_type", "user")
            sender_type = raw_sender_type if raw_sender_type in ("user", "agent") else "user"
            sender_avatar = body.get("sender_avatar", "")
            sender_color = body.get("sender_color", "")
            thread_id = body.get("thread_id")

            # Only auto-route to @-agents when the source message itself
            # is a user post — bot replies persisted via this endpoint
            # already happened through /chat and shouldn't trigger a
            # second agent invocation.
            mentions: list[str] = []
            if sender_type == "user":
                for app in self.registry.list_apps():
                    patterns = [f"@{app['name'].lower()}", f"@{app['id']}"]
                    for p in patterns:
                        if p in content.lower():
                            mentions.append(app["id"])
                            break

            user_msg = svc.send_message(
                channel_id=channel["id"],
                sender_type=sender_type,
                sender_id=sender_id,
                sender_name=sender_name,
                sender_avatar=sender_avatar,
                sender_color=sender_color,
                content=content,
                thread_id=thread_id,
                mentions=mentions,
                tools_used=body.get("tools_used", []),
            )
            result = {"user_message": user_msg, "agent_messages": []}

            # Process @agent mentions — get AI responses.
            #
            if not body.get("dispatch", True):
                mentions = []

            for agent_id in mentions:
                app = self.registry.get(agent_id)
                if not app:
                    continue
                # Check agent is a member of this channel
                members = svc.list_channel_members(channel["id"])
                is_member = any(
                    m["member_type"] == "agent" and m["member_id"] == agent_id for m in members
                )
                if not is_member:
                    continue
                # Get AI response
                try:
                    # Clean the mention from the text for the agent
                    clean = content
                    for p in [f"@{app.name}", f"@{agent_id}"]:
                        clean = re.sub(re.escape(p), "", clean, flags=re.IGNORECASE).strip()

                    session_id = f"channel-{slug}-{agent_id}"
                    ai_result = await self.registry.chat(agent_id, clean or content, session_id)
                    agent_msg = svc.send_message(
                        channel_id=channel["id"],
                        sender_type="agent",
                        sender_id=agent_id,
                        sender_name=app.name,
                        sender_avatar=app.avatar,
                        sender_color=app.color,
                        content=ai_result["text"],
                        thread_id=thread_id,
                        tools_used=ai_result.get("tools_used", []),
                    )
                    result["agent_messages"].append(agent_msg)
                except Exception as e:
                    log.error("Agent %s failed in channel %s: %s", agent_id, slug, e)

            return result

        @r.get("/channels/{slug}/threads/{thread_id}")
        async def get_thread(slug: str, thread_id: str):
            svc = _require_messaging()
            messages = svc.get_thread_messages(thread_id)
            return {"messages": messages}

        @r.patch("/messages/{message_id}")
        async def edit_message(message_id: str, body: dict):
            svc = _require_messaging()
            content = body.get("content", "")
            if not content:
                raise HTTPException(400, "content required")
            return svc.update_message(message_id, content)

        @r.delete("/messages/{message_id}")
        async def delete_message(message_id: str):
            svc = _require_messaging()
            return svc.delete_message(message_id)

        @r.post("/messages/{message_id}/reactions")
        async def toggle_reaction(message_id: str, body: dict):
            svc = _require_messaging()
            emoji = body.get("emoji", "")
            user_id = body.get("user_id", "")
            if not emoji or not user_id:
                raise HTTPException(400, "emoji and user_id required")
            return svc.add_reaction(message_id, emoji, user_id)

        @r.post("/channels/{slug}/read")
        async def mark_channel_read(slug: str, body: dict):
            svc = _require_messaging()
            channel = svc.get_channel_by_slug(slug)
            if not channel:
                raise HTTPException(404, f"Channel '{slug}' not found")
            user_id = body.get("user_id", "")
            if not user_id:
                raise HTTPException(400, "user_id required")
            svc.mark_read(channel["id"], "user", user_id)
            return {"ok": True}

        @r.get("/unread")
        async def get_unread(user_id: str):
            svc = _require_messaging()
            return {"counts": svc.get_unread_counts(user_id)}

        @r.get("/channels/{slug}/members")
        async def list_channel_members(slug: str):
            svc = _require_messaging()
            channel = svc.get_channel_by_slug(slug)
            if not channel:
                raise HTTPException(404, f"Channel '{slug}' not found")
            return {"members": svc.list_channel_members(channel["id"])}

        @r.post("/channels/{slug}/members")
        async def add_channel_member(slug: str, body: dict):
            svc = _require_messaging()
            channel = svc.get_channel_by_slug(slug)
            if not channel:
                raise HTTPException(404, f"Channel '{slug}' not found")
            member_type = body.get("member_type", "")
            member_id = body.get("member_id", "")
            member_name = body.get("member_name", "")
            if not member_type or not member_id or not member_name:
                raise HTTPException(400, "member_type, member_id, member_name required")
            return svc.add_channel_member(
                channel["id"],
                member_type,
                member_id,
                member_name,
                role=body.get("role", "member"),
            )

        @r.delete("/channels/{slug}/members/{member_type}/{member_id}")
        async def remove_channel_member(slug: str, member_type: str, member_id: str):
            svc = _require_messaging()
            channel = svc.get_channel_by_slug(slug)
            if not channel:
                raise HTTPException(404, f"Channel '{slug}' not found")
            svc.remove_channel_member(channel["id"], member_type, member_id)
            return {"ok": True}
