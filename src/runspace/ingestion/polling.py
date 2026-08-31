"""Telegram long-polling transport."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from ._redact import redact

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# Long-poll window; Telegram caps this at 50 but 25 is plenty for
# our throughput and gives the connection room to cycle if a
# Heroku-style proxy idle-times-out.
LONG_POLL_TIMEOUT_S = 25

# How often to re-log a sustained 409 conflict. The first 409 always
# logs; subsequent ones inside this window are silently counted.
# A permanent misconfiguration — two hosts polling one token — therefore
# stays visible without flooding the journal on every poll.
CONFLICT_LOG_INTERVAL_S = 300.0


class TelegramPollingTransport:
    """Long-poll transport for Telegram. One instance per tenant.

    Construction takes a callback (`handle`) that mirrors the
    webhook route's signature — the polling loop just produces the
    same kind of Telegram update payloads.
    """

    provider = "telegram"

    def __init__(
        self,
        *,
        tenant_id: str,
        bot_token: str,
        handle: Callable[[dict], Awaitable[dict]],
        offset_dir: str | Path | None = None,
        offset_path: str | Path | None = None,
    ) -> None:
        """Either `offset_dir` (legacy: writes `.telegram-offset.json`
        under it) or `offset_path` (multi-bot: caller chooses the
        exact file, e.g. `.telegram-offset-ada.json`). Exactly one
        is required; explicit `offset_path` wins when both supplied.
        """
        self._tenant_id = tenant_id
        self._bot_token = bot_token
        self._handle = handle
        if offset_path is not None:
            self._offset_path = Path(offset_path)
        elif offset_dir is not None:
            self._offset_path = Path(offset_dir) / ".telegram-offset.json"
        else:
            raise ValueError("offset_dir or offset_path required")
        self._task: asyncio.Task | None = None
        self._running = False
        self._client: httpx.AsyncClient | None = None
        # Conflict state: surfaced via `status()` so the admin UI can
        # show "another deployment is polling this token" instead of
        # the user only seeing "No DM senders yet" and not knowing why.
        self._conflict_since: float | None = None
        self._conflict_count: int = 0
        self._conflict_last_logged: float = 0.0

    # ── lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._client = httpx.AsyncClient(timeout=LONG_POLL_TIMEOUT_S + 5)
        self._task = asyncio.create_task(self._loop(), name=f"tg-poll-{self._tenant_id}")
        log.info("[tg-poll] started for tenant %s", self._tenant_id)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        log.info("[tg-poll] stopped for tenant %s", self._tenant_id)

    async def send(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_to: int | None = None,
        thread_id: int | str | None = None,
    ) -> None:
        """Outbound delivery. Mirrors `telegram._send_reply` so
        webhook and polling paths reach the API the same way."""
        from .telegram import _send_reply

        await _send_reply(self._bot_token, chat_id, text, reply_to=reply_to, thread_id=thread_id)

    # ── internals ─────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """The long-poll loop. Continues until `stop()` flips _running.

        Errors are logged + slept-on; we never raise out of the loop
        because that'd kill the only inbound channel for this tenant.
        Telegram's getUpdates is idempotent on offset, so retrying
        after a timeout / 5xx just resumes from the same place.
        """
        offset = self._read_offset()
        backoff = 1.0
        while self._running:
            try:
                params = {
                    "timeout": LONG_POLL_TIMEOUT_S,
                    "allowed_updates": json.dumps(
                        [
                            "message",
                            "edited_message",
                            "channel_post",
                            # Bot membership changes (added/removed/banned).
                            # Lets us auto-remove an external_channels binding
                            # when the owner kicks the bot from a group.
                            "my_chat_member",
                        ]
                    ),
                }
                if offset is not None:
                    params["offset"] = offset
                assert self._client is not None
                r = await self._client.get(
                    f"{TELEGRAM_API}/bot{self._bot_token}/getUpdates",
                    params=params,
                )
                if r.status_code == 409:
                    # Telegram allows only one process per token. Cause:
                    # another deployment (staging/dev) is also calling
                    # getUpdates on this token, OR a webhook is still
                    # registered. Surface it loudly once, then throttle.
                    self._note_conflict()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue
                r.raise_for_status()
                # Past raise_for_status with a non-409 → not in conflict.
                if self._conflict_since is not None:
                    log.warning(
                        "[tg-poll] conflict cleared for tenant %s after %d retries",
                        self._tenant_id,
                        self._conflict_count,
                    )
                    self._conflict_since = None
                    self._conflict_count = 0
                    self._conflict_last_logged = 0.0
                payload = r.json()
                if not payload.get("ok"):
                    log.warning("[tg-poll] getUpdates not ok: %r", payload)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue
                backoff = 1.0  # success → reset
                updates = payload.get("result") or []
                for upd in updates:
                    try:
                        await self._handle(upd)
                    except Exception as e:
                        log.exception(
                            "[tg-poll] handler raised on update %s: %s",
                            upd.get("update_id"),
                            redact(e),
                        )
                    # Advance past this update regardless — `getUpdates`
                    # treats offset = max_seen + 1.
                    if isinstance(upd.get("update_id"), int):
                        offset = upd["update_id"] + 1
                if updates:
                    self._write_offset(offset)
                else:
                    # No updates in the long-poll window. Telegram's
                    # getUpdates has its own server-side wait, so this
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException:
                # Telegram returned no updates in the window. That's
                # how long-poll *normally* exits — loop right back.
                continue
            except Exception as e:
                # Network blip, 5xx, JSON parse, …
                log.warning(
                    "[tg-poll] loop error (%s): %s; sleeping %.1fs",
                    type(e).__name__,
                    redact(e),
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    # ── conflict surfacing ────────────────────────────────────────────

    def _note_conflict(self) -> None:
        """Record a 409 occurrence. First one logs at WARNING with the
        full cause hint; subsequent ones are silently counted unless
        CONFLICT_LOG_INTERVAL_S has elapsed since the last log line.
        """
        now = time.monotonic()
        first = self._conflict_since is None
        self._conflict_count += 1
        if first:
            self._conflict_since = now
            self._conflict_last_logged = now
            log.warning(
                "[tg-poll] HTTP 409 Conflict for tenant %s — another process "
                "is polling this bot token (other deployment? stale webhook?). "
                "Will keep retrying but throttling further log lines.",
                self._tenant_id,
            )
            return
        if now - self._conflict_last_logged >= CONFLICT_LOG_INTERVAL_S:
            self._conflict_last_logged = now
            elapsed = int(now - self._conflict_since)
            log.warning(
                "[tg-poll] HTTP 409 Conflict for tenant %s — still ongoing (%d attempts over %ds).",
                self._tenant_id,
                self._conflict_count,
                elapsed,
            )

    def status(self) -> dict:
        """Snapshot for admin UI / health probes. Returns the loop's
        running state and the conflict counter so operators can tell
        a healthy poller from one stuck on 409 without grepping logs.
        """
        return {
            "running": self._running,
            "conflict": self._conflict_since is not None,
            "conflict_count": self._conflict_count,
            "conflict_since_monotonic": self._conflict_since,
        }

    # ── offset persistence ────────────────────────────────────────────

    def _read_offset(self) -> int | None:
        if not self._offset_path.exists():
            return None
        try:
            data = json.loads(self._offset_path.read_text())
            v = data.get("offset")
            return int(v) if v is not None else None
        except Exception as e:
            log.warning("[tg-poll] could not read offset: %s", redact(e))
            return None

    def _write_offset(self, offset: int | None) -> None:
        if offset is None:
            return
        self._offset_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._offset_path.with_suffix(self._offset_path.suffix + ".tmp")
        tmp.write_text(json.dumps({"offset": int(offset)}))
        os.replace(tmp, self._offset_path)
