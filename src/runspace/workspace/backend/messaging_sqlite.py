"""Channels, threads and direct messages on SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_channels (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    slug         TEXT NOT NULL,
    description  TEXT DEFAULT '',
    icon         TEXT DEFAULT 'Hash',
    is_default   INTEGER DEFAULT 0,
    is_archived  INTEGER DEFAULT 0,
    created_by   TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    UNIQUE (tenant_id, slug)
);

CREATE TABLE IF NOT EXISTS workspace_messages (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    channel_id    TEXT NOT NULL,
    thread_id     TEXT,
    sender_type   TEXT NOT NULL,
    sender_id     TEXT NOT NULL,
    sender_name   TEXT NOT NULL,
    sender_avatar TEXT DEFAULT '',
    sender_color  TEXT DEFAULT '',
    content       TEXT NOT NULL,
    tools_used    TEXT DEFAULT '[]',
    attachments   TEXT DEFAULT '[]',
    mentions      TEXT DEFAULT '[]',
    metadata      TEXT DEFAULT '{}',
    reactions     TEXT DEFAULT '{}',
    is_deleted    INTEGER DEFAULT 0,
    edited_at     TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_channel_members (
    channel_id  TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,
    member_type TEXT NOT NULL,
    member_id   TEXT NOT NULL,
    member_name TEXT NOT NULL,
    role        TEXT DEFAULT 'member',
    last_read_at TEXT,
    PRIMARY KEY (channel_id, member_type, member_id)
);

CREATE INDEX IF NOT EXISTS idx_msg_channel ON workspace_messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_thread  ON workspace_messages(thread_id);
"""

_JSON_COLUMNS = ("tools_used", "attachments", "mentions", "metadata", "reactions")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> Path:
    """`MESSAGING_DB_PATH`, else a project-local file."""
    raw = os.environ.get("MESSAGING_DB_PATH")
    return Path(raw) if raw else Path.cwd() / ".runspace" / "messaging.sqlite"


class SqliteMessagingService:
    """Channels on a local file. Same surface as the Supabase service."""

    def __init__(self, tenant_id: str, db_path: str | Path | None = None):
        self.tenant_id = tenant_id
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for col in _JSON_COLUMNS:
            if col in d and isinstance(d[col], str):
                try:
                    d[col] = json.loads(d[col])
                except json.JSONDecodeError:
                    d[col] = [] if col != "metadata" and col != "reactions" else {}
        for col in ("is_default", "is_archived", "is_deleted"):
            if col in d:
                d[col] = bool(d[col])
        return d

    # ── channels ─────────────────────────────────────────────────────────

    def list_channels(self, include_dm: bool = False) -> list[dict]:
        sql = (
            "SELECT * FROM workspace_channels WHERE tenant_id = ? AND is_archived = 0 "
            "ORDER BY is_default DESC, name"
        )
        with self._conn() as c:
            out = [self._row(r) for r in c.execute(sql, (self.tenant_id,))]
        if not include_dm:
            out = [ch for ch in out if not (ch or {}).get("slug", "").startswith("dm-")]
        return [ch for ch in out if ch]

    def create_channel(
        self,
        name: str,
        slug: str,
        created_by: str = "",
        description: str = "",
        icon: str = "Hash",
        is_default: bool = False,
    ) -> dict:
        existing = self.get_channel_by_slug(slug)
        if existing:
            return existing
        cid = str(uuid.uuid4())
        with self._conn() as c:
            c.execute(
                "INSERT INTO workspace_channels (id, tenant_id, name, slug, description, icon,"
                " is_default, is_archived, created_by, created_at) VALUES (?,?,?,?,?,?,?,0,?,?)",
                (
                    cid,
                    self.tenant_id,
                    name,
                    slug,
                    description,
                    icon,
                    int(is_default),
                    created_by,
                    _now(),
                ),
            )
        return self.get_channel_by_slug(slug) or {}

    def get_channel_by_slug(self, slug: str) -> dict | None:
        with self._conn() as c:
            return self._row(
                c.execute(
                    "SELECT * FROM workspace_channels WHERE tenant_id = ? AND slug = ?",
                    (self.tenant_id, slug),
                ).fetchone()
            )

    # ── messages ─────────────────────────────────────────────────────────

    def get_channel_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: str | None = None,
        include_deleted: bool = False,
    ) -> list[dict]:
        sql = "SELECT * FROM workspace_messages WHERE tenant_id = ? AND channel_id = ?"
        args: list[Any] = [self.tenant_id, channel_id]
        if not include_deleted:
            sql += " AND is_deleted = 0"
        if before:
            sql += " AND created_at < ?"
            args.append(before)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            rows = [self._row(r) for r in c.execute(sql, tuple(args))]
        return [r for r in reversed(rows) if r]

    def get_thread_messages(self, thread_id: str, include_deleted: bool = False) -> list[dict]:
        sql = "SELECT * FROM workspace_messages WHERE tenant_id = ? AND thread_id = ?"
        if not include_deleted:
            sql += " AND is_deleted = 0"
        sql += " ORDER BY created_at"
        with self._conn() as c:
            return [
                r for r in (self._row(x) for x in c.execute(sql, (self.tenant_id, thread_id))) if r
            ]

    def send_message(
        self,
        channel_id: str,
        sender_type: str,
        sender_id: str,
        sender_name: str,
        content: str,
        thread_id: str | None = None,
        sender_avatar: str = "",
        sender_color: str = "",
        tools_used: list[str] | None = None,
        attachments: list[dict] | None = None,
        mentions: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        mid = str(uuid.uuid4())
        with self._conn() as c:
            c.execute(
                "INSERT INTO workspace_messages (id, tenant_id, channel_id, thread_id, sender_type,"
                " sender_id, sender_name, sender_avatar, sender_color, content, tools_used,"
                " attachments, mentions, metadata, reactions, is_deleted, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'{}',0,?)",
                (
                    mid,
                    self.tenant_id,
                    channel_id,
                    thread_id,
                    sender_type,
                    sender_id,
                    sender_name,
                    sender_avatar,
                    sender_color,
                    content,
                    json.dumps(tools_used or []),
                    json.dumps(attachments or []),
                    json.dumps(mentions or []),
                    json.dumps(metadata or {}),
                    _now(),
                ),
            )
            return (
                self._row(
                    c.execute("SELECT * FROM workspace_messages WHERE id = ?", (mid,)).fetchone()
                )
                or {}
            )

    def update_message(self, message_id: str, content: str) -> dict:
        with self._conn() as c:
            c.execute(
                "UPDATE workspace_messages SET content = ?, edited_at = ? WHERE id = ? AND tenant_id = ?",
                (content, _now(), message_id, self.tenant_id),
            )
            return (
                self._row(
                    c.execute(
                        "SELECT * FROM workspace_messages WHERE id = ?", (message_id,)
                    ).fetchone()
                )
                or {}
            )

    def delete_message(self, message_id: str) -> dict:
        with self._conn() as c:
            c.execute(
                "UPDATE workspace_messages SET is_deleted = 1 WHERE id = ? AND tenant_id = ?",
                (message_id, self.tenant_id),
            )
            return (
                self._row(
                    c.execute(
                        "SELECT * FROM workspace_messages WHERE id = ?", (message_id,)
                    ).fetchone()
                )
                or {}
            )

    def add_reaction(self, message_id: str, emoji: str, user_id: str) -> dict:
        with self._conn() as c:
            row = c.execute(
                "SELECT reactions FROM workspace_messages WHERE id = ? AND tenant_id = ?",
                (message_id, self.tenant_id),
            ).fetchone()
            if row is None:
                return {}
            try:
                reactions = json.loads(row["reactions"] or "{}")
            except json.JSONDecodeError:
                reactions = {}
            who = set(reactions.get(emoji, []))
            who.symmetric_difference_update({user_id})  # a second press removes it
            if who:
                reactions[emoji] = sorted(who)
            else:
                reactions.pop(emoji, None)
            c.execute(
                "UPDATE workspace_messages SET reactions = ? WHERE id = ?",
                (json.dumps(reactions), message_id),
            )
            return (
                self._row(
                    c.execute(
                        "SELECT * FROM workspace_messages WHERE id = ?", (message_id,)
                    ).fetchone()
                )
                or {}
            )

    # ── membership and read state ────────────────────────────────────────

    def mark_read(self, channel_id: str, member_type: str, member_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO workspace_channel_members (channel_id, tenant_id, member_type,"
                " member_id, member_name, last_read_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(channel_id, member_type, member_id)"
                " DO UPDATE SET last_read_at = excluded.last_read_at",
                (channel_id, self.tenant_id, member_type, member_id, member_id, _now()),
            )

    def get_unread_counts(self, user_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._conn() as c:
            for ch in c.execute(
                "SELECT id FROM workspace_channels WHERE tenant_id = ? AND is_archived = 0",
                (self.tenant_id,),
            ):
                seen = c.execute(
                    "SELECT last_read_at FROM workspace_channel_members WHERE channel_id = ?"
                    " AND member_type = 'user' AND member_id = ?",
                    (ch["id"], user_id),
                ).fetchone()
                since = (seen["last_read_at"] if seen else None) or "1970-01-01"
                n = c.execute(
                    "SELECT COUNT(*) n FROM workspace_messages WHERE channel_id = ? AND is_deleted = 0"
                    " AND created_at > ? AND NOT (sender_type = 'user' AND sender_id = ?)",
                    (ch["id"], since, user_id),
                ).fetchone()["n"]
                if n:
                    counts[ch["id"]] = n
        return counts

    def add_channel_member(
        self,
        channel_id: str,
        member_type: str,
        member_id: str,
        member_name: str,
        role: str = "member",
    ) -> dict:
        with self._conn() as c:
            c.execute(
                "INSERT INTO workspace_channel_members (channel_id, tenant_id, member_type,"
                " member_id, member_name, role) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(channel_id, member_type, member_id)"
                " DO UPDATE SET member_name = excluded.member_name, role = excluded.role",
                (channel_id, self.tenant_id, member_type, member_id, member_name, role),
            )
        return {
            "channel_id": channel_id,
            "member_type": member_type,
            "member_id": member_id,
            "member_name": member_name,
            "role": role,
        }

    def remove_channel_member(self, channel_id: str, member_type: str, member_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "DELETE FROM workspace_channel_members WHERE channel_id = ?"
                " AND member_type = ? AND member_id = ?",
                (channel_id, member_type, member_id),
            )

    def list_channel_members(self, channel_id: str) -> list[dict]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM workspace_channel_members WHERE channel_id = ? AND tenant_id = ?"
                    " ORDER BY member_type, member_name",
                    (channel_id, self.tenant_id),
                )
            ]

    # ── bootstrap ────────────────────────────────────────────────────────

    def ensure_default_channels(self, channels_config: list[dict], agents: dict[str, Any]) -> None:
        """Seed channels declared in workspace.yml, and put every agent in them.

        Only entries with `type: chat` (the default) become channels; `addon`
        and `activity` are navigation entries.
        """
        for cfg in channels_config or []:
            if cfg.get("type", "chat") != "chat":
                continue
            slug = cfg.get("id", "")
            if not slug:
                continue
            channel = self.get_channel_by_slug(slug) or self.create_channel(
                name=cfg.get("label") or slug,
                slug=slug,
                icon=cfg.get("icon", "Hash"),
                is_default=slug == "general",
            )
            for app_id, app in (agents or {}).items():
                self.add_channel_member(
                    channel["id"], "agent", app_id, app.get("name", app_id), role="member"
                )
