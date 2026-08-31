"""Pairing state — DM access approvals."""

from __future__ import annotations

import datetime as _dt
import fcntl
import json
import logging
import os
import secrets
import string
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

PAIRING_TTL_HOURS = 1
PAIRING_CODE_LEN = 6
# Hard cap on pending bucket size to limit a stranger's ability to
# bloat .pairings.json by spamming new account ids. The transport
MAX_PENDING_PAIRINGS_HARD_CAP = 50
_CODE_ALPHABET = string.ascii_uppercase + string.digits  # I/O/0/1 are visually
# ambiguous but the
# owner sees the code


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _generate_code(existing: Iterable[str]) -> str:
    existing_set = set(existing)
    for _ in range(50):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(PAIRING_CODE_LEN))
        if code not in existing_set:
            return code
    raise RuntimeError(
        "could not generate unique pairing code (50 attempts exhausted; pending table likely huge)"
    )


class FilePairingState:
    """Per-tenant pairing record store, backed by one json file.

    `path` is the json file (typically
    `tenants/<id>/.pairings.json`). The directory must exist; the
    file is created on first write.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")

    # ── public surface ────────────────────────────────────────────────

    def is_authorized(self, sender_id: str) -> bool:
        data = self._read()
        return str(sender_id) in data.get("approved", {})

    def request(
        self,
        *,
        sender_id: str,
        sender_handle: str,
        chat_id: str,
        provider: str = "telegram",
    ) -> str:
        """Create a pending pairing record. Returns the pairing code
        the bot should reply with. Idempotent on `sender_id`: if the
        sender already has a non-expired pending code, return that
        same code instead of generating a new one (avoids spamming
        the owner with new approve buttons every time the user re-
        DMs).
        """
        with self._lock():
            data = self._read_unlocked()
            pending = data.setdefault("pending", {})

            # Look for an existing non-expired pending for this sender.
            now = _dt.datetime.now(_dt.timezone.utc)
            for code, rec in list(pending.items()):
                if str(rec.get("sender_id")) != str(sender_id):
                    continue
                expires = self._parse_iso(rec.get("expires_at"))
                if expires and expires > now:
                    return code
                # Expired — drop it.
                pending.pop(code, None)

            # Backstop the bucket size. Drop oldest non-expired
            # pending if we're at the hard cap. The transport layer
            # should refuse before reaching here; this protects file
            # size if anything ever bypasses that.
            if len(pending) >= MAX_PENDING_PAIRINGS_HARD_CAP:
                oldest = sorted(
                    pending.items(),
                    key=lambda kv: kv[1].get("created_at", ""),
                )[0]
                pending.pop(oldest[0], None)

            code = _generate_code(pending.keys())
            pending[code] = {
                "sender_id": str(sender_id),
                "sender_handle": sender_handle,
                "chat_id": str(chat_id),
                "provider": provider,
                "created_at": _now_iso(),
                "expires_at": (now + _dt.timedelta(hours=PAIRING_TTL_HOURS)).isoformat(),
            }
            self._write_unlocked(data)
            return code

    def approve(self, code: str) -> dict | None:
        """Approve a pending code → moves to `approved`. Returns the
        approved entry, or None if the code is unknown / expired."""
        with self._lock():
            data = self._read_unlocked()
            pending = data.setdefault("pending", {})
            approved = data.setdefault("approved", {})

            entry = pending.pop(code, None)
            if entry is None:
                self._write_unlocked(data)
                log.info("[pairing] approve: code %r not found", code)
                return None
            now = _dt.datetime.now(_dt.timezone.utc)
            expires = self._parse_iso(entry.get("expires_at"))
            if expires and expires < now:
                self._write_unlocked(data)
                log.info(
                    "[pairing] approve: code %r expired (sender_id=%s)",
                    code,
                    entry.get("sender_id"),
                )
                return None
            new_record = {
                "sender_id": entry["sender_id"],
                "sender_handle": entry["sender_handle"],
                "provider": entry.get("provider", "telegram"),
                "approved_at": _now_iso(),
                "pairing_code": code,
            }
            approved[entry["sender_id"]] = new_record
            self._write_unlocked(data)
            log.info(
                "[pairing] approved sender_id=%s handle=%s provider=%s",
                new_record["sender_id"],
                new_record["sender_handle"],
                new_record["provider"],
            )
            return new_record

    def revoke(self, sender_id: str) -> bool:
        """Remove an approved sender. Idempotent: returns True if a
        record was actually removed, False if no such id was
        approved."""
        with self._lock():
            data = self._read_unlocked()
            approved = data.setdefault("approved", {})
            entry = approved.pop(str(sender_id), None)
            if entry is None:
                self._write_unlocked(data)
                log.info("[pairing] revoke: sender_id=%s was not in approved set", sender_id)
                return False
            revoked_list = data.setdefault("revoked", [])
            entry["revoked_at"] = _now_iso()
            revoked_list.append(entry)
            # Cap the revoked log at 200 entries — auditing, not history.
            if len(revoked_list) > 200:
                data["revoked"] = revoked_list[-200:]
            self._write_unlocked(data)
            log.info(
                "[pairing] revoked sender_id=%s handle=%s", sender_id, entry.get("sender_handle")
            )
            return True

    def list_pending(self) -> list[dict]:
        """All non-expired pending records, oldest first."""
        data = self._read()
        now = _dt.datetime.now(_dt.timezone.utc)
        out: list[dict] = []
        for code, rec in (data.get("pending") or {}).items():
            expires = self._parse_iso(rec.get("expires_at"))
            if expires and expires < now:
                continue
            out.append({"code": code, **rec})
        out.sort(key=lambda r: r.get("created_at", ""))
        return out

    def list_approved(self) -> list[dict]:
        data = self._read()
        approved = data.get("approved") or {}
        return list(approved.values())

    # ── internals ─────────────────────────────────────────────────────

    def _read(self) -> dict:
        with self._lock():
            return self._read_unlocked()

    def _read_unlocked(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return {}

    def _write_unlocked(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, self._path)

    @contextmanager
    def _lock(self):
        """Sidecar-file flock — same pattern as WorkspaceRoutinesStore.
        The data file gets rotated by `os.replace`, which swaps
        inodes and would otherwise leave parallel flock holders on
        orphans; the sidecar stays put across rotations.
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _parse_iso(s: str | None) -> _dt.datetime | None:
        if not s:
            return None
        try:
            v = _dt.datetime.fromisoformat(s)
            if v.tzinfo is None:
                v = v.replace(tzinfo=_dt.timezone.utc)
            return v
        except Exception:
            return None


# ── DM policy resolution + multi-bot config ──────────────────────────


def resolve_telegram_settings(workspace_cfg: dict) -> dict:
    """First-bot resolver — returns the first configured bot's dict.

    Used by call sites that don't have an explicit `bot_config` yet
    (e.g. unscoped helpers in tests). For real per-bot decisions,
    pass an explicit `bot_config` to the helpers below.
    """
    bots = resolve_telegram_bots(workspace_cfg)
    return bots[0] if bots else {}


def resolve_telegram_bots(workspace_cfg: dict) -> list[dict]:
    """Return the list of configured Telegram bots for this tenant.

    The single canonical YAML shape is:

      messaging:
        telegram_bots:
          - name: ada
            token: ${ACME_..._ADA}
            transport: polling
            dmPolicy: pairing
            dmAgent: accountant
          - name: max
            token: ${ACME_..._MAX}
            transport: polling
            dmPolicy: open
            dmAgent: booking

    Each returned bot dict has these keys (filled in / normalised):
      - name              (str, required identifier)
      - token             (str, may use ${ENV} substitution at the
                           `_bot_token` resolution layer)
      - transport         (str, defaults to "webhook")
      - dmPolicy          (str)
      - dmAgent           (str | None)
      - allowFrom         (list)
      - pairing_filename  (str, `.pairings-<name>.json`)
      - offset_filename   (str, `.telegram-offset-<name>.json`)

    Anonymous entries (no `name`) are silently skipped — names are
    required to namespace state files and webhook routes.
    """
    msg = workspace_cfg.get("messaging") or {}
    if not isinstance(msg, dict):
        return []
    raw_bots = msg.get("telegram_bots")
    if not isinstance(raw_bots, list):
        return []
    out: list[dict] = []
    for entry in raw_bots:
        if not isinstance(entry, dict):
            continue
        bot = dict(entry)
        name = (bot.get("name") or "").strip()
        if not name:
            continue
        bot["name"] = name
        bot["pairing_filename"] = f".pairings-{name}.json"
        bot["offset_filename"] = f".telegram-offset-{name}.json"
        out.append(bot)
    return out


def resolve_dm_policy(workspace_cfg: dict, bot_config: dict | None = None) -> str:
    """One of: pairing | allowlist | open | disabled.

    When called with an explicit `bot_config`, reads from that bot's
    dict. Without it, falls back to the first bot (legacy / single-
    bot tenants).

    Defaults to `pairing`. Unknown values fall back to `disabled`
    rather than `open` — fail-safe.
    """
    cfg = bot_config if bot_config is not None else resolve_telegram_settings(workspace_cfg)
    raw = (cfg.get("dmPolicy") or "pairing").strip().lower()
    if raw in ("pairing", "allowlist", "open", "disabled"):
        return raw
    return "disabled"


def resolve_allow_list(workspace_cfg: dict, bot_config: dict | None = None) -> set[str]:
    """Numeric Telegram user IDs allow-listed for DMs (per-bot)."""
    cfg = bot_config if bot_config is not None else resolve_telegram_settings(workspace_cfg)
    raw = cfg.get("allowFrom") or []
    out: set[str] = set()
    for entry in raw:
        s = str(entry).strip()
        for prefix in ("telegram:", "tg:"):
            if s.lower().startswith(prefix):
                s = s[len(prefix) :]
        if s:
            out.add(s)
    return out
