"""Telegram inbound handler for external channels."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from ._redact import redact
from ._render import transform_for_telegram
from .buffer import get_buffer


def _workspace_public_url() -> str | None:
    """Optional absolute URL for Telegram deep-links into the web workspace.

    Set per-tenant via env (e.g. `WORKSPACE_PUBLIC_URL=https://acme.example`).
    When unset, the renderer omits the "Open in workspace" tap-through —
    Telegram still gets the degraded table, just without a link.
    """
    url = os.environ.get("WORKSPACE_PUBLIC_URL")
    return url.strip() if url and url.strip() else None


log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# Defense-in-depth limits. Telegram itself caps text at 4096 chars
# but a malicious sender can still flood with paragraphs aimed at
# burning LLM tokens. Truncate at 4000 (matches our outbound cap).
MAX_DM_TEXT_LEN = 4000
# Cap how many pending pairings a tenant can accumulate before we
# refuse new ones. Stops a stranger from creating thousands of
# .pairings.json entries by spinning up new accounts. The legitimate
# steady-state is 1–10; 50 is a generous ceiling.
MAX_PENDING_PAIRINGS = 50


def _safe(s: str | None, *, limit: int = 64) -> str:
    """Sanitize a user-supplied string for safe inclusion in logs.

    Strips control characters (newlines, tabs, NUL) which would
    otherwise let an attacker forge log lines, and truncates to
    `limit` chars. Returns `<empty>` for None/empty.
    """
    if not s:
        return "<empty>"
    cleaned = "".join(c for c in s if c.isprintable() and c not in ("\r", "\n", "\t"))
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "…"
    return cleaned or "<empty>"


# ── Webhook auth ────────────────────────────────────────────────────────


def verify_secret(tenant_id: str, header_value: str | None) -> bool:
    """Match the X-Telegram-Bot-Api-Secret-Token header against an
    env-var secret. Per-tenant via TELEGRAM_WEBHOOK_SECRET_<TENANT>;
    falls back to a single TELEGRAM_WEBHOOK_SECRET for shared
    deployments. None means auth is unconfigured — refuse all
    requests in that case rather than silently accept."""
    expected = os.environ.get(f"TELEGRAM_WEBHOOK_SECRET_{tenant_id.upper().replace('-', '_')}")
    if not expected:
        expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not expected or not header_value:
        return False
    return header_value == expected


# ── Binding lookup ──────────────────────────────────────────────────────


def _find_binding(
    workspace_cfg: dict, chat_id: str | int, bot_name: str | None = None
) -> dict | None:
    """Match update.message.chat.id against external_channels in
    workspace.yml. Returns the binding dict, or None if no match —
    the caller should silently 200 to keep Telegram from retrying.

    Multi-bot tenants can scope a binding to a specific bot via
    `binding.bot: <bot_name>`. If omitted, the binding matches any
    bot (so legacy single-bot configs keep working). When `bot_name`
    is supplied, prefer a bot-scoped binding over an unscoped one
    (more specific wins).
    """
    chat_id_str = str(chat_id)
    unscoped: dict | None = None
    for b in workspace_cfg.get("external_channels") or []:
        if b.get("provider") != "telegram":
            continue
        if str(b.get("chat_id")) != chat_id_str:
            continue
        b_bot = b.get("bot")
        if b_bot and bot_name and b_bot == bot_name:
            return b
        if not b_bot:
            unscoped = unscoped or b
    return unscoped


def _pairing_path_for(
    tenant_id: str, workspace_cfg: dict, bot_config: dict | None = None
) -> Path | None:
    """Resolve the pairing-state file path for a given bot.

    Multi-bot tenants get one file per bot:
      `.pairings-ada.json`, `.pairings-max.json`, …
    Legacy single-bot tenants keep `.pairings.json` (no name suffix)
    so that an upgrade doesn't strand existing approvals.

    Returns None when we can't locate the tenant directory
    (workspace.yml loaded as a synthetic dict in unit tests).
    """
    base = workspace_cfg.get("_base_dir")
    if not base:
        return None
    filename = (bot_config or {}).get("pairing_filename") or ".pairings.json"
    return Path(base) / filename


def _resolve_env_token(raw: str) -> str:
    """Substitute `${ENV_VAR}` references in a token string."""
    if raw.startswith("${") and raw.endswith("}"):
        return os.environ.get(raw[2:-1], "")
    return raw


def _bot_token(workspace_cfg: dict, bot_config: dict | None = None) -> str | None:
    """Resolve the bot token from the bot's `token` field.

    Tokens live exclusively under `messaging.telegram_bots[].token`
    in workspace.yml, with `${ENV_VAR}` substitution honoured so the
    actual secret stays in env, never on disk.

    `bot_config` is required for a successful lookup. Without it we
    fall back to the first configured bot, which keeps single-bot
    tenants working when a caller hasn't been threaded with
    `bot_config` yet.
    """
    if bot_config is None:
        from .pairing import resolve_telegram_bots

        bots = resolve_telegram_bots(workspace_cfg)
        bot_config = bots[0] if bots else {}
    raw = (bot_config or {}).get("token") or ""
    tok = _resolve_env_token(raw)
    return tok or None


# ── Update routing ──────────────────────────────────────────────────────


async def handle_update(
    *,
    tenant_id: str,
    workspace_cfg: dict,
    update: dict,
    app_registry: Any,
    callbacks: Any = None,  # CallbackHandlerRegistry
    bot_config: dict | None = None,
) -> dict:
    """Single entry point for Telegram updates. Returns a small status
    dict the route can echo back. Never raises — webhook handlers
    must always 200 unless a transport-level error is genuinely
    transient (which is none of these cases).

    `bot_config` carries the per-bot slice of workspace.yml when the
    transport knows which bot delivered this update. The polling
    transport always passes it (one transport instance per bot). The
    legacy webhook route may not, in which case we fall back to the
    first configured bot — sufficient for single-bot tenants and the
    common multi-bot case where webhook routes are namespaced per bot
    upstream of this function.
    """
    if bot_config is None:
        # Default to the first configured bot. Single-bot tenants
        # (legacy `messaging.telegram: {...}`) get a single entry
        # automatically; multi-bot tenants reaching this fallback
        # would need a webhook route that names the bot upstream.
        from .pairing import resolve_telegram_bots

        bots = resolve_telegram_bots(workspace_cfg)
        bot_config = bots[0] if bots else {}

    # ── Bot membership changes → auto-cleanup bindings ────────
    # Telegram emits `my_chat_member` when the bot's status in a chat
    if "my_chat_member" in update:
        cm = update["my_chat_member"] or {}
        new_status = (cm.get("new_chat_member") or {}).get("status") or ""
        chat_obj = cm.get("chat") or {}
        chat_id_n = chat_obj.get("id")
        # Statuses that mean "the bot can no longer participate":
        # left, kicked. (member/administrator/restricted = still in.)
        if new_status in ("left", "kicked") and chat_id_n is not None:
            try:
                _autoremove_binding_on_leave(
                    tenant_id=tenant_id,
                    workspace_cfg=workspace_cfg,
                    bot_config=bot_config,
                    chat_id=chat_id_n,
                )
            except Exception as e:
                log.warning("[telegram] autoremove on leave failed: %s", redact(e))
            log.info(
                "[telegram] bot %s removed from chat %s (status=%s); "
                "binding + discovery entry cleaned up",
                bot_config.get("name"),
                chat_id_n,
                new_status,
            )
            return {
                "ok": True,
                "left_chat": True,
                "chat_id": str(chat_id_n),
                "bot": bot_config.get("name"),
            }

    # ── Route callback_query to its registered handler ────────────
    # Inline-button clicks arrive as `callback_query` updates, not
    # as messages. Route them by `callback_data` prefix to whatever
    # the platform registered. Always answer the callback (Telegram
    # shows a spinner on the button until we do).
    if "callback_query" in update:
        cq = update["callback_query"] or {}
        if callbacks is None:
            log.warning(
                "[telegram] callback_query received but no "
                "callback registry configured; sender=%s data=%s",
                (cq.get("from") or {}).get("id"),
                cq.get("data"),
            )
            await _answer_callback(
                workspace_cfg, cq.get("id"), text="No handler configured", bot_config=bot_config
            )
            return {"ignored": "no_callback_registry"}
        data = cq.get("data") or ""
        handler = callbacks.lookup(data)
        if handler is None:
            await _answer_callback(
                workspace_cfg, cq.get("id"), text="Unknown action", bot_config=bot_config
            )
            return {"ignored": "no_callback_match", "data": data}
        try:
            result = await handler(
                {
                    "provider": "telegram",
                    "callback_data": data,
                    "sender_id": str((cq.get("from") or {}).get("id") or ""),
                    "chat_id": str(((cq.get("message") or {}).get("chat") or {}).get("id") or ""),
                    "message_id": (cq.get("message") or {}).get("message_id"),
                    "raw": cq,
                }
            )
        except Exception as e:
            log.exception("[telegram] callback handler raised: %s", redact(e))
            await _answer_callback(workspace_cfg, cq.get("id"), text="Internal error")
            return {"error": "callback_failed", "detail": str(e)[:200]}
        ack_text = (result or {}).get("ack") or "Done"
        await _answer_callback(workspace_cfg, cq.get("id"), text=ack_text, bot_config=bot_config)
        return {"ok": True, "callback_data": data, "result": result}

    msg = update.get("message") or update.get("edited_message") or update.get("channel_post") or {}
    if not msg:
        return {"ignored": "no message in update"}

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ignored": "no chat.id"}

    sender = msg.get("from") or {}
    sender_id = str(sender.get("id") or "")
    sender_handle = sender.get("username") or sender_id or "unknown"

    # ── DM access policy gate ──────────────────────────
    # Group and supergroup messages flow through the existing
    chat_type = (chat.get("type") or "").lower()
    if chat_type == "private":
        from .pairing import (
            FilePairingState,
            resolve_allow_list,
            resolve_dm_policy,
        )

        policy = resolve_dm_policy(workspace_cfg, bot_config)
        if policy == "disabled":
            return {
                "ignored": "dm_disabled",
                "sender": _safe(sender_handle),
                "bot": bot_config.get("name"),
            }
        if policy == "allowlist":
            allow = resolve_allow_list(workspace_cfg, bot_config)
            if sender_id not in allow:
                return {
                    "ignored": "not_in_allowlist",
                    "sender": _safe(sender_handle),
                    "bot": bot_config.get("name"),
                }
        elif policy == "pairing":
            pairing_path = _pairing_path_for(tenant_id, workspace_cfg, bot_config)
            pairing = FilePairingState(pairing_path) if pairing_path else None
            if pairing and not pairing.is_authorized(sender_id):
                # Defense against pairing-flood: if the pending bucket
                # is already at the cap and this sender doesn't already
                # have a pending entry, refuse silently. Existing
                # pending senders are still served (idempotent path).
                if len(pairing.list_pending()) >= MAX_PENDING_PAIRINGS and not any(
                    p["sender_id"] == sender_id for p in pairing.list_pending()
                ):
                    log.warning(
                        "[telegram] pairing bucket full (>=%d) for tenant %s; "
                        "rejecting new request from sender_id=%s handle=%s",
                        MAX_PENDING_PAIRINGS,
                        tenant_id,
                        _safe(sender_id),
                        _safe(sender_handle),
                    )
                    return {
                        "ignored": "pairing_bucket_full",
                        "sender": _safe(sender_handle),
                        "bot": bot_config.get("name"),
                    }
                code = pairing.request(
                    sender_id=sender_id,
                    sender_handle=sender_handle,
                    chat_id=str(chat_id),
                )
                bot_token = _bot_token(workspace_cfg, bot_config)
                if bot_token:
                    try:
                        await _send_reply(
                            bot_token,
                            chat_id,
                            (
                                f"👋 Hi @{sender_handle}, you're not authorised "
                                f"to DM this bot yet.\n\nAsk the workspace owner "
                                f"to approve pairing code: *{code}*\n\n"
                                f"(Code is valid for 1 hour.)"
                            ),
                            reply_to=msg.get("message_id"),
                        )
                    except Exception as e:
                        log.warning("[telegram] pairing reply failed: %s", redact(e))
                return {
                    "pending_pairing": True,
                    "code": code,
                    "sender": _safe(sender_handle),
                    "bot": bot_config.get("name"),
                }
            # else: open — fall through

        # ── Pass policy → DM is authorised. Route to the agent
        # configured for THIS bot (`bot_config.dmAgent`). Each bot in
        # a multi-bot tenant pins one dmAgent — that's the whole
        # point of running separate bots (different agent identity,
        # different policy, different audience).
        dm_agent = bot_config.get("dmAgent")
        if not dm_agent:
            log.warning(
                "[telegram] DM authorised but no dmAgent on bot %s for tenant %s",
                bot_config.get("name"),
                tenant_id,
            )
            return {
                "ignored": "no_dm_agent_configured",
                "sender": _safe(sender_handle),
                "bot": bot_config.get("name"),
            }
        # Per-(bot, sender) session — different bots talking to the
        # same Telegram user keep separate conversation histories so
        # Ada's invoice context doesn't leak into Max's bookings.
        bot_name = bot_config.get("name") or "default"
        session_id = f"telegram:{tenant_id}:{bot_name}:{sender_id}"

        # ── File DM → process via the agent (same flow as groups) ─────
        # Trust gate is the pairing approval already passed above; no
        # `trusted_senders` allow-list needed for DMs because the
        # owner explicitly approved this sender_id.
        file_obj, file_kind = _extract_file(msg)
        if file_obj:
            user_caption = (msg.get("caption") or "").strip()
            return await _process_file_via_agent(
                tenant_id=tenant_id,
                workspace_cfg=workspace_cfg,
                msg=msg,
                chat_id=chat_id,
                file_obj=file_obj,
                file_kind=file_kind,
                agent_id=dm_agent,
                app_registry=app_registry,
                prefix_caption=user_caption,
                session_id=session_id,
                thread_id=None,
                bot_config=bot_config,
                chat_type=chat_type,
            )

        text = msg.get("text") or ""
        # Cap inbound text before passing to the LLM. Telegram caps at
        # 4096 chars, but a malicious sender could chain messages or
        # abuse a higher-limit client to burn tokens.
        if len(text) > MAX_DM_TEXT_LEN:
            text = text[:MAX_DM_TEXT_LEN]
        if not text:
            # Truly non-text, non-file DM (sticker, voice, location).
            # Drop with a hint — calling the agent with no payload
            # would just confuse it.
            bot_token = _bot_token(workspace_cfg, bot_config)
            if bot_token:
                try:
                    await _send_reply(
                        bot_token,
                        chat_id,
                        "I can handle text and document/photo uploads here. "
                        "Stickers, voice and location aren't supported yet.",
                        reply_to=msg.get("message_id"),
                    )
                except Exception as e:
                    log.warning("[telegram] DM non-text ack failed: %s", redact(e))
            return {
                "ignored": "dm_unsupported_type",
                "kinds": list(msg.keys()),
                "sender": _safe(sender_handle),
                "bot": bot_config.get("name"),
            }

        # Show "typing..." while the LLM is working (~2-4s for most
        # turns). Best-effort, swallowed on failure.
        bot_token_for_action = _bot_token(workspace_cfg, bot_config)
        if bot_token_for_action:
            try:
                await _send_chat_action(bot_token_for_action, chat_id, "typing")
            except Exception:
                pass

        try:
            result = await app_registry.chat(dm_agent, text, session_id)
        except Exception as e:
            log.exception(
                "[telegram] DM agent.chat failed for tenant %s bot %s: %s",
                tenant_id,
                bot_config.get("name"),
                redact(e),
            )
            bot_token = _bot_token(workspace_cfg, bot_config)
            if bot_token:
                try:
                    await _send_reply(
                        bot_token,
                        chat_id,
                        f"🔴 Internal error: {type(e).__name__}",
                        reply_to=msg.get("message_id"),
                    )
                except Exception as e2:
                    log.warning("[telegram] DM error reply failed: %s", e2)
            return {"error": "agent_failed", "detail": str(e)[:200]}

        reply_text = (result or {}).get("text", "").strip() or "(empty)"
        # Degrade ```datatable```/```chart``` fences to plain
        # markdown for Telegram. chat_type is "private" here.
        reply_text = transform_for_telegram(
            reply_text,
            chat_type=chat_type,
            workspace_url=_workspace_public_url(),
        )
        bot_token = _bot_token(workspace_cfg, bot_config)
        if bot_token:
            try:
                await _send_reply(bot_token, chat_id, reply_text, reply_to=msg.get("message_id"))
            except Exception as e:
                log.warning("[telegram] DM reply send failed: %s", redact(e))
        return {
            "ok": True,
            "agent": dm_agent,
            "session": session_id,
            "bot": bot_config.get("name"),
            "tools_used": (result or {}).get("tools_used", []) if result else [],
        }

    # ── Group / supergroup: bot-aware binding gate ────────────────────
    binding = _find_binding(workspace_cfg, chat_id, bot_config.get("name"))
    if not binding:
        # Auto-discovery: record this chat so the owner can bind it
        # via the workspace UI without having to hunt down the chat_id
        # by hand. One file per bot under the tenant base dir; updates
        # in place on every fresh sighting (last_seen + count).
        try:
            _record_discovered_chat(
                workspace_cfg=workspace_cfg,
                bot_config=bot_config,
                chat=chat,
                sender_handle=sender_handle,
                msg=msg,
            )
        except Exception as e:
            log.warning("[telegram] discovery record failed: %s", redact(e))
        log.info(
            "[telegram] unbound chat %s (%s, type=%s) for bot %s; available for binding",
            chat_id,
            _safe(chat.get("title") or ""),
            (chat.get("type") or ""),
            bot_config.get("name"),
        )
        return {
            "ignored": "unbound chat",
            "chat_id": str(chat_id),
            "bot": bot_config.get("name"),
            "discovered": True,
        }

    # Reply threading: when the inbound came from a topic in a
    # group with topics enabled, the reply must carry the same
    # message_thread_id so it lands in the same topic. Telegram's
    # supergroup forum threads attach this on every message in a
    # topic. Plain chats and rooms-without-topics omit it.
    thread_id = msg.get("message_thread_id")

    # ── Text in groups: always buffer; respond if the bot is tagged ───
    # Buffering keeps surrounding chat available as caption context for
    if "text" in msg and "document" not in msg and "photo" not in msg:
        buf = get_buffer(
            tenant_id,
            str(chat_id),
            max_messages=int(binding.get("context_max_messages", 20)),
            window_seconds=int(binding.get("context_window_seconds", 600)),
        )
        buf.push(
            sender=f"@{sender_handle}", text=msg["text"], ts=float(msg.get("date", time.time()))
        )

        identity = await _get_bot_identity(workspace_cfg, bot_config)
        if identity and _message_addresses_bot(msg, identity):
            agent_id = binding.get("agent")
            if not agent_id:
                return {
                    "buffered": True,
                    "addressed": True,
                    "ignored_response": "binding_missing_agent",
                    "bot": bot_config.get("name"),
                }
            text = _strip_bot_mention(msg["text"], identity.get("username") or "")
            if len(text) > MAX_DM_TEXT_LEN:
                text = text[:MAX_DM_TEXT_LEN]
            if not text.strip():
                return {
                    "buffered": True,
                    "addressed": True,
                    "ignored_response": "empty_after_strip",
                    "bot": bot_config.get("name"),
                }
            # One session per (bot, chat, thread). Per-bot scoping
            # keeps Ada's group history separate from Max's even
            # when both are in the same chat.
            bot_name = bot_config.get("name") or "default"
            session_id = f"telegram:{tenant_id}:{bot_name}:group:{chat_id}"
            if thread_id is not None:
                session_id += f":{thread_id}"
            # "typing..." indicator for the in-group LLM turn.
            bot_token_for_action = _bot_token(workspace_cfg, bot_config)
            if bot_token_for_action:
                try:
                    await _send_chat_action(bot_token_for_action, chat_id, "typing")
                except Exception:
                    pass
            try:
                result = await app_registry.chat(agent_id, text, session_id)
            except Exception as e:
                log.exception(
                    "[telegram] group tagged-chat failed for tenant %s bot %s: %s",
                    tenant_id,
                    bot_config.get("name"),
                    redact(e),
                )
                bot_token = _bot_token(workspace_cfg, bot_config)
                if bot_token:
                    try:
                        await _send_reply(
                            bot_token,
                            chat_id,
                            f"🔴 Internal error: {type(e).__name__}",
                            reply_to=msg.get("message_id"),
                            thread_id=thread_id,
                        )
                    except Exception as e2:
                        log.warning("[telegram] group error reply failed: %s", e2)
                return {"error": "agent_failed", "detail": str(e)[:200]}
            reply_text = (result or {}).get("text", "").strip() or "(empty)"
            # Degrade widgets to markdown. chat_type is the
            # group/supergroup variant here — narrower table cap.
            reply_text = transform_for_telegram(
                reply_text,
                chat_type=chat_type,
                workspace_url=_workspace_public_url(),
            )
            bot_token = _bot_token(workspace_cfg, bot_config)
            if bot_token:
                try:
                    await _send_reply(
                        bot_token,
                        chat_id,
                        reply_text,
                        reply_to=msg.get("message_id"),
                        thread_id=thread_id,
                    )
                except Exception as e:
                    log.warning("[telegram] group reply send failed: %s", redact(e))
            return {
                "ok": True,
                "agent": agent_id,
                "addressed": True,
                "session": session_id,
                "bot": bot_config.get("name"),
                "tools_used": (result or {}).get("tools_used", []) if result else [],
            }

        return {"buffered": True, "chat_id": str(chat_id)}

    # ── Document or photo → process via the bound agent ────────────
    file_obj, file_kind = _extract_file(msg)
    if not file_obj:
        # Non-file, non-text message in a bound group (sticker, voice,
        # location, …). Silently drop — the team didn't expect a reply.
        return {
            "ignored": "unsupported message type",
            "kinds": list(msg.keys()),
            "thread_id": thread_id,
        }

    # Trusted-sender check (only enforced when the binding lists any).
    trusted = binding.get("trusted_senders") or []
    if trusted and sender_id not in [str(s) for s in trusted]:
        log.info(
            "[telegram] dropping file from untrusted sender %s in chat %s",
            _safe(sender_handle),
            chat_id,
        )
        return {"ignored": "untrusted_sender", "sender": _safe(sender_handle)}

    # Render caption from the buffer NOW, before any IO that might
    # take seconds — we want the snapshot to reflect what the team
    # said up to this file.
    buf = get_buffer(
        tenant_id,
        str(chat_id),
        max_messages=int(binding.get("context_max_messages", 20)),
        window_seconds=int(binding.get("context_window_seconds", 600)),
    )
    caption_block = buf.render()
    user_caption = (msg.get("caption") or "").strip()
    full_caption = caption_block
    if user_caption:
        full_caption = (full_caption + f"\n\n[file caption]\n{user_caption}").strip()

    agent_id = binding.get("agent")
    if not agent_id:
        return {"error": "binding_missing_agent"}

    bot_name = bot_config.get("name") or "default"
    return await _process_file_via_agent(
        tenant_id=tenant_id,
        workspace_cfg=workspace_cfg,
        msg=msg,
        chat_id=chat_id,
        file_obj=file_obj,
        file_kind=file_kind,
        agent_id=agent_id,
        app_registry=app_registry,
        prefix_caption=full_caption,
        session_id=f"telegram:{tenant_id}:{bot_name}:{chat_id}:{msg.get('message_id', 'x')}",
        thread_id=thread_id,
        bot_config=bot_config,
        chat_type=chat_type,
    )


# ── Auto-cleanup when the bot is removed from a chat ──────────────────


def _autoremove_binding_on_leave(
    *, tenant_id: str, workspace_cfg: dict, bot_config: dict | None, chat_id: int | str
) -> None:
    """When the bot is removed/kicked from a Telegram chat, drop the
    matching `external_channels` entry from workspace.yml AND clear
    the discovery record. The binding is dead the moment the bot
    can't see messages in that chat — leaving it stale is a foot-
    gun (UI shows "approved" for a chat the bot can't participate
    in anymore).

    Mutates `workspace.yml` on disk (atomic-rename) AND
    `workspace_cfg` in place so the polling task's closure sees the
    change immediately. Best-effort: failures are logged + swallowed
    by the caller.
    """
    import yaml as _yaml

    base = workspace_cfg.get("_base_dir")
    if not base:
        return
    bot_name = (bot_config or {}).get("name") or ""
    chat_id_s = str(chat_id)

    # 1. Remove the binding from workspace.yml
    yml_path = Path(base) / "workspace.yml"
    if yml_path.exists():
        with open(yml_path) as f:
            disk = _yaml.safe_load(f) or {}
        old_bindings = list(disk.get("external_channels") or [])
        kept = [
            b
            for b in old_bindings
            if not (
                b.get("provider") == "telegram"
                and str(b.get("chat_id")) == chat_id_s
                and (not b.get("bot") or b.get("bot") == bot_name)
            )
        ]
        if len(kept) != len(old_bindings):
            disk["external_channels"] = kept
            tmp = yml_path.with_suffix(yml_path.suffix + ".tmp")
            tmp.write_text(
                _yaml.safe_dump(
                    disk,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            )
            os.replace(tmp, yml_path)
            # Reflect the disk change in the running config in place.
            workspace_cfg["external_channels"] = kept

    # 2. Clear the discovery record (so it doesn't reappear in the
    #    "Pending" list every time the bot is briefly re-added).
    disc_path = _discovered_chats_path(workspace_cfg, bot_config)
    if disc_path and disc_path.exists():
        try:
            data = json.loads(disc_path.read_text())
        except Exception:
            data = {}
        if isinstance(data, dict) and chat_id_s in data:
            del data[chat_id_s]
            tmp2 = disc_path.with_suffix(disc_path.suffix + ".tmp")
            tmp2.write_text(json.dumps(data, indent=2, sort_keys=True))
            os.replace(tmp2, disc_path)


# ── Auto-discovery of unbound chats ────────────────────────────────────


def _discovered_chats_path(workspace_cfg: dict, bot_config: dict | None) -> Path | None:
    """One file per bot under the tenant base dir, namespaced by bot
    name. Returns None when the cfg is synthetic (no _base_dir) so
    unit tests don't accidentally write to the host filesystem."""
    base = workspace_cfg.get("_base_dir")
    if not base:
        return None
    name = (bot_config or {}).get("name") or "default"
    return Path(base) / f".discovered-chats-{name}.json"


def _record_discovered_chat(
    *,
    workspace_cfg: dict,
    bot_config: dict | None,
    chat: dict,
    sender_handle: str,
    msg: dict | None = None,
) -> None:
    """Record an inbound chat that has no binding so the owner can
    surface it in the UI and click "bind" without having to hunt down
    the chat_id manually.

    Schema (one entry per chat_id):
      {
        "<chat_id>": {
          "type":        "supergroup" | "group" | "channel" | "private",
          "title":       "<group title or None for DMs>",
          "first_seen":  ISO timestamp,
          "last_seen":   ISO timestamp,
          "count":       int,
          "last_sender": "@handle",
          "inviter":     "@sam"   (set once on the bot-added event)
        }
      }

    Updates last_seen + count on every sighting; first_seen + inviter
    only on the very first record. Capped at 100 entries (oldest
    dropped) to avoid unbounded growth from a malicious actor adding
    the bot to many groups.
    """
    path = _discovered_chats_path(workspace_cfg, bot_config)
    if not path:
        return
    chat_id = chat.get("id")
    if chat_id is None:
        return
    chat_id_s = str(chat_id)
    now = _now_iso()
    try:
        if path.exists():
            data = json.loads(path.read_text())
        else:
            data = {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    entry = data.get(chat_id_s) or {}
    entry["type"] = chat.get("type") or entry.get("type") or "unknown"
    title = chat.get("title")
    if title:
        entry["title"] = title
    elif "title" not in entry:
        entry["title"] = None
    is_new = "first_seen" not in entry
    entry.setdefault("first_seen", now)
    entry["last_seen"] = now
    entry["count"] = int(entry.get("count") or 0) + 1
    entry["last_sender"] = f"@{sender_handle}" if sender_handle else None

    # Inviter is set ONCE on first sighting. If we have a
    # `new_chat_members` event (someone explicitly added the bot),
    # prefer that over the first regular-message sender — it's the
    # most accurate "who added the bot here" signal for audit.
    if "inviter" not in entry:
        m = msg or {}
        new_members = m.get("new_chat_members") or []
        if isinstance(new_members, list) and new_members:
            inviter_user = m.get("from") or {}
            inv_handle = inviter_user.get("username") or inviter_user.get("first_name") or ""
            if inv_handle:
                entry["inviter"] = f"@{inv_handle}"
            elif inviter_user.get("id"):
                entry["inviter"] = f"id:{inviter_user.get('id')}"
        elif is_new and sender_handle:
            # Fallback: first message sender becomes the implicit
            # inviter (best-effort when we missed the join event).
            entry["inviter"] = f"@{sender_handle}"
    data[chat_id_s] = entry

    # Cap at 100 — drop oldest by last_seen if we exceed.
    if len(data) > 100:
        ordered = sorted(data.items(), key=lambda kv: kv[1].get("last_seen", ""))
        data = dict(ordered[-100:])

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _now_iso() -> str:
    """Local helper to avoid importing datetime in many places."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ── Bot identity (cached) for group "tagged" detection ────────────────


# Cache by token so a multi-tenant deployment with multiple bots keeps
# one identity per bot. Avoids hitting `getMe` on every group message.
_BOT_IDENTITY_CACHE: dict[str, dict] = {}


async def _get_bot_identity(workspace_cfg: dict, bot_config: dict | None = None) -> dict | None:
    """Return `{id, username}` for the bot, fetching once per token.

    Used to detect group messages that address the bot (mention of
    `@<username>` or reply to one of the bot's messages). Returns
    `None` if the token is missing or `getMe` fails — in that case
    the caller falls through to bare-buffer behaviour (no tagged-
    response detection), which is the safe degradation.

    Multi-bot tenants pass `bot_config` so the right token is used
    (each bot has its own identity); the cache is keyed by token
    string so different bots get separate entries automatically.
    """
    token = _bot_token(workspace_cfg, bot_config)
    if not token:
        return None
    cached = _BOT_IDENTITY_CACHE.get(token)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{TELEGRAM_API}/bot{token}/getMe")
            r.raise_for_status()
            body = r.json()
        result = body.get("result") if body.get("ok") else None
        if not result:
            log.warning("[telegram] getMe returned non-ok: %r", body)
            return None
        identity = {
            "id": result.get("id"),
            "username": (result.get("username") or "").lower(),
        }
        _BOT_IDENTITY_CACHE[token] = identity
        return identity
    except Exception as e:
        log.warning("[telegram] getMe failed: %s", redact(e))
        return None


def _message_addresses_bot(msg: dict, identity: dict) -> bool:
    """True when the message tags or replies to the bot.

    Tagging signals (any one is sufficient):
      1. `entities[]` includes a `mention` whose text matches
         `@<bot_username>` (case-insensitive).
      2. `entities[]` includes a `text_mention` whose `user.id`
         matches the bot's id (used by clients that link mentions
         without writing the @username literally).
      3. `reply_to_message.from.id` matches the bot's id — i.e. the
         user is replying to one of Ada's previous messages.

    We do NOT respond to `bot_command` entities here. Slash commands
    (`/start`, `/help`) need their own handler; treating them as
    free-text would be confusing (Ada isn't a command bot).
    """
    bot_id = identity.get("id")
    bot_username = (identity.get("username") or "").lower()
    text = msg.get("text") or ""
    entities = msg.get("entities") or []
    for ent in entities:
        etype = ent.get("type")
        if etype == "mention" and bot_username:
            offset = int(ent.get("offset", 0))
            length = int(ent.get("length", 0))
            mention_text = text[offset : offset + length].lstrip("@").lower()
            if mention_text == bot_username:
                return True
        elif etype == "text_mention":
            user = ent.get("user") or {}
            if bot_id and user.get("id") == bot_id:
                return True
    reply_to = msg.get("reply_to_message") or {}
    reply_from = reply_to.get("from") or {}
    if bot_id and reply_from.get("id") == bot_id:
        return True
    return False


def _strip_bot_mention(text: str, bot_username: str) -> str:
    """Remove a leading `@<bot_username>` (and the space after it) from
    the message text. The agent doesn't need the mention — leaving it
    in tends to confuse the LLM into talking about itself.

    Example:
      "@acme_ada_bot what's overdue?" → "what's overdue?"
    """
    if not bot_username:
        return text.strip()
    handle = "@" + bot_username
    stripped = text.lstrip()
    # Case-insensitive prefix check; preserve original case for the rest.
    if stripped.lower().startswith(handle.lower()):
        return stripped[len(handle) :].lstrip()
    return text.strip()


# ── File-handling helper (shared by group + DM paths) ─────────────────


def _extract_file(msg: dict) -> tuple[dict | None, str | None]:
    """Pull the file object + kind from an inbound update.

    Telegram puts documents under `document` and photos under `photo`
    (an array of size variants — pick the largest). Other media types
    (`voice`, `sticker`, `video`, `audio`, `animation`) are intentionally
    NOT extracted: the file-handling pipeline targets invoice scans
    and similar artefacts, not arbitrary chat media.
    """
    if "document" in msg:
        return msg["document"], "document"
    if "photo" in msg:
        photos = msg["photo"] or []
        if photos:
            biggest = max(photos, key=lambda p: p.get("file_size") or 0)
            return biggest, "photo"
    return None, None


async def _process_file_via_agent(
    *,
    tenant_id: str,
    workspace_cfg: dict,
    msg: dict,
    chat_id: int | str,
    file_obj: dict,
    file_kind: str,
    agent_id: str,
    app_registry: Any,
    prefix_caption: str,
    session_id: str,
    thread_id: int | str | None,
    bot_config: dict | None = None,
    chat_type: str = "private",
) -> dict:
    """Download → FileStorage.put → agent.chat(process_invoice) → reply.

    Shared by both group flow (caption = buffered group context +
    user file caption) and DM flow (caption = user file caption only,
    DMs have no context buffer).

    Trust is enforced upstream:
      - groups: `binding.trusted_senders` allow-list (caller checks)
      - DMs:    pairing approval passed before reaching here

    Errors are mapped to status dicts; we never raise out of the
    handler so the polling/webhook loop stays alive on transient
    failures (network, OCR errors, agent crashes).
    """
    bot_token = _bot_token(workspace_cfg, bot_config)
    if not bot_token:
        log.warning(
            "[telegram] no bot token configured for tenant %s bot %s",
            tenant_id,
            (bot_config or {}).get("name"),
        )
        return {"error": "no_bot_token"}

    # UX: show "uploading document…" indicator in Telegram so the user
    # sees progress during the ~5-10 s OCR+save window. Best-effort —
    # the action expires after 5s if not refreshed; we send it once
    # which covers the typical OCR latency.
    try:
        action = "upload_photo" if file_kind == "photo" else "upload_document"
        await _send_chat_action(bot_token, chat_id, action)
    except Exception:
        pass

    try:
        file_bytes, original_name = await _download_telegram_file(
            bot_token,
            file_obj,
            file_kind=file_kind,
        )
    except Exception as e:
        log.exception("[telegram] download failed for tenant %s", tenant_id)
        return {"error": "download_failed", "detail": str(e)[:200]}

    try:
        from runspace.protocols import get_file_storage

        storage = get_file_storage()
        meta = storage.put(
            tenant_id,
            original_name,
            file_bytes,
            content_type=file_obj.get("mime_type") or "application/octet-stream",
        )
    except Exception as e:
        log.exception("[telegram] storage put failed for tenant %s", tenant_id)
        return {"error": "storage_failed", "detail": str(e)[:200]}

    prompt = (
        f"Process this invoice from a Telegram message. "
        f"Use process_invoice with file_ref={original_name!r}. "
        f"After you have the result, reply with ONE line that "
        f"substitutes real values from the tool result, like:\n"
        f"  Acme_Supplies_Ltd — €103,94, due 2024-06-06\n"
        f"Do NOT echo the template literally; use the actual "
        f"supplier, formatted amount, and due_date returned by the "
        f"tool. If extraction or saving failed, reply with the "
        f"reason in one line. Never expose confidence or "
        f"raw_extraction."
    )
    if prefix_caption:
        prompt = prefix_caption + "\n\n" + prompt

    try:
        result = await app_registry.chat(agent_id, prompt, session_id)
    except Exception as e:
        log.exception("[telegram] agent.chat failed for tenant %s", tenant_id)
        try:
            await _send_reply(
                bot_token,
                chat_id,
                f"🔴 Internal error processing the file: {type(e).__name__}",
                reply_to=msg.get("message_id"),
                thread_id=thread_id,
            )
        except Exception as e2:
            log.warning("[telegram] file error reply failed: %s", e2)
        return {"error": "agent_failed", "detail": str(e)[:200]}

    reply_text = (result or {}).get("text", "").strip() or "(empty)"
    # Degrade widgets to markdown for the file-flow reply too —
    # process_invoice now returns an approval card built from a datatable.
    reply_text = transform_for_telegram(
        reply_text,
        chat_type=chat_type,
        workspace_url=_workspace_public_url(),
    )
    try:
        await _send_reply(
            bot_token, chat_id, reply_text, reply_to=msg.get("message_id"), thread_id=thread_id
        )
    except Exception as e:
        log.warning("[telegram] file reply send failed: %s", redact(e))

    return {
        "ok": True,
        "agent": agent_id,
        "file_id": meta.file_id,
        "tools_used": (result or {}).get("tools_used", []) if result else [],
    }


# ── Telegram API helpers ────────────────────────────────────────────────


async def _download_telegram_file(
    bot_token: str,
    file_obj: dict,
    *,
    file_kind: str,
) -> tuple[bytes, str]:
    """Two-step Telegram CDN download:
    1. POST /getFile?file_id=… → returns file_path
    2. GET /file/bot<token>/<file_path> → bytes
    """
    file_id = file_obj.get("file_id")
    if not file_id:
        raise ValueError("file_obj has no file_id")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(
            f"{TELEGRAM_API}/bot{bot_token}/getFile",
            params={"file_id": file_id},
        )
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"getFile failed: {body!r}")
        file_path = body["result"]["file_path"]

        cdn = await client.get(f"{TELEGRAM_API}/file/bot{bot_token}/{file_path}")
        cdn.raise_for_status()
        data = cdn.content

    # original_name = filename if document had one, else derived
    if file_kind == "document":
        original_name = file_obj.get("file_name") or Path(file_path).name
    else:
        # Photos don't carry a filename; synthesize from file_id + extension
        ext = Path(file_path).suffix or ".jpg"
        original_name = f"telegram_{file_id[:12]}{ext}"
    return data, original_name


async def _send_reply(
    bot_token: str,
    chat_id: int | str,
    text: str,
    *,
    reply_to: int | None = None,
    thread_id: int | str | None = None,
    buttons: list[list[dict]] | None = None,
) -> None:
    """sendMessage with optional reply, topic threading, and inline
    keyboard.

    `thread_id` → Telegram's `message_thread_id`.
    `buttons` → 2D array of `{label, callback_data}` dicts mapped
    to Telegram's `inline_keyboard` shape.
    """
    payload: dict = {"chat_id": chat_id, "text": text[:4000]}
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": b["label"], "callback_data": b["callback_data"]} for b in row]
                for row in buttons
            ],
        }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{TELEGRAM_API}/bot{bot_token}/sendMessage",
            json=payload,
        )
        r.raise_for_status()


async def _leave_chat(bot_token: str, chat_id: int | str) -> bool:
    """Make the bot leave a group/channel/supergroup.

    Telegram returns 200/`ok=True` on success and on already-not-in
    cases. Best-effort — caller logs failures.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{TELEGRAM_API}/bot{bot_token}/leaveChat",
            json={"chat_id": chat_id},
        )
        try:
            body = r.json()
        except Exception:
            body = {}
        return bool(body.get("ok"))


async def _send_chat_action(bot_token: str, chat_id: int | str, action: str = "typing") -> None:
    """Show a "typing..." / "uploading..." indicator in the chat.

    Telegram auto-expires the indicator after 5 s (or when we send the
    next message). We call this once at the start of a slow operation
    (OCR, agent.chat) so users see "the bot is working" instead of
    silence. Best-effort — failures swallowed since this is pure UX.

    Common `action` values:
      - "typing"            — text response coming
      - "upload_document"   — file processing
      - "upload_photo"      — photo processing
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{TELEGRAM_API}/bot{bot_token}/sendChatAction",
                json={"chat_id": chat_id, "action": action},
            )
    except Exception as e:
        log.debug("[telegram] sendChatAction failed (non-fatal): %s", redact(e))


async def _answer_callback(
    workspace_cfg: dict,
    callback_query_id: str | None,
    *,
    text: str = "",
    bot_config: dict | None = None,
) -> None:
    """Telegram requires an explicit `answerCallbackQuery` per click,
    otherwise the inline button shows a loading spinner indefinitely.
    Best-effort — failures are logged + swallowed."""
    if not callback_query_id:
        return
    bot_token = _bot_token(workspace_cfg, bot_config)
    if not bot_token:
        return
    payload = {"callback_query_id": callback_query_id, "text": text[:200]}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{TELEGRAM_API}/bot{bot_token}/answerCallbackQuery",
                json=payload,
            )
    except Exception as e:
        log.warning("[telegram] answerCallbackQuery failed: %s", redact(e))
