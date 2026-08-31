"""Workspace Messaging Service — channels and messages backed by Supabase."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supabase client (module-level singleton, service_role key recommended)

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        from supabase import create_client  # lazy

        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY required")
        _client = create_client(url, key)
    return _client


# ---------------------------------------------------------------------------
# MessagingService
# ---------------------------------------------------------------------------


class MessagingService:
    """Channel-based messaging for a single tenant."""

    def __init__(self, tenant_id: str, client: Client | None = None):
        self.tenant_id = tenant_id
        self._db = client or _get_client()

    # -- Channels -----------------------------------------------------------

    def list_channels(self, include_dm: bool = False) -> list[dict]:
        """Return all non-archived channels for this tenant.

        By default, auto-generated DM channels (slug starts with
        `dm-`) are filtered out — those are per-agent direct-message
        rooms created by the chat persistence path; surfacing them
        in the admin Channels list is noise (one row per agent at
        best, dozens of `dm-routine:acme:ada`-style ghosts at
        worst when session IDs leaked into the slug).

        Pass `include_dm=True` for the chat path that legitimately
        wants to know about a specific agent's DM channel.
        """
        res = (
            self._db.table("workspace_channels")
            .select("*")
            .eq("tenant_id", self.tenant_id)
            .is_("archived_at", "null")
            .order("created_at")
            .execute()
        )
        rows = res.data or []
        if include_dm:
            return rows
        return [r for r in rows if not str(r.get("slug", "")).lower().startswith("dm-")]

    def create_channel(
        self,
        name: str,
        slug: str,
        created_by: str = "",
        description: str = "",
        icon: str = "Hash",
        is_default: bool = False,
    ) -> dict:
        res = (
            self._db.table("workspace_channels")
            .insert(
                {
                    "tenant_id": self.tenant_id,
                    "name": name,
                    "slug": slug,
                    "created_by": created_by,
                    "description": description,
                    "icon": icon,
                    "is_default": is_default,
                }
            )
            .execute()
        )
        return (res.data or [{}])[0]

    def get_channel_by_slug(self, slug: str) -> dict | None:
        res = (
            self._db.table("workspace_channels")
            .select("*")
            .eq("tenant_id", self.tenant_id)
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    # -- Messages -----------------------------------------------------------

    def get_channel_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: str | None = None,
        include_deleted: bool = False,
    ) -> list[dict]:
        """List a channel's top-level messages (oldest-first within the limit window).

        Soft-deleted rows (`deleted = true`) are excluded by default — they
        shouldn't surface in normal feeds. Pass `include_deleted=True` only for
        admin/audit views.
        """
        q = (
            self._db.table("workspace_messages")
            .select("*")
            .eq("channel_id", channel_id)
            .is_("thread_id", "null")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if not include_deleted:
            q = q.eq("deleted", False)
        if before:
            q = q.lt("created_at", before)
        res = q.execute()
        # Return in chronological order (oldest first)
        data = res.data or []
        data.reverse()
        return data

    def get_thread_messages(self, thread_id: str, include_deleted: bool = False) -> list[dict]:
        """Replies in a thread, oldest-first. Same `deleted = false` default
        as `get_channel_messages`."""
        q = (
            self._db.table("workspace_messages")
            .select("*")
            .eq("thread_id", thread_id)
            .order("created_at")
        )
        if not include_deleted:
            q = q.eq("deleted", False)
        res = q.execute()
        return res.data or []

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
        # WORKSPACE_WRITE_DISABLED guard — set on staging to prevent it
        # from posting to the same #general as the production tenant
        if os.environ.get("WORKSPACE_WRITE_DISABLED", "").lower() in ("1", "true", "yes"):
            log.info(
                "workspace write disabled — skipping send_message (channel=%s, sender=%s/%s)",
                channel_id,
                sender_type,
                sender_id,
            )
            return {
                "id": "skipped-write-disabled",
                "tenant_id": self.tenant_id,
                "channel_id": channel_id,
                "skipped": True,
                "reason": "WORKSPACE_WRITE_DISABLED",
            }

        row = {
            "tenant_id": self.tenant_id,
            "channel_id": channel_id,
            "sender_type": sender_type,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "sender_avatar": sender_avatar,
            "sender_color": sender_color,
            "content": content,
            "tools_used": tools_used or [],
            "attachments": attachments or [],
            "mentions": mentions or [],
            "metadata": metadata or {},
        }
        if thread_id:
            row["thread_id"] = thread_id
        res = self._db.table("workspace_messages").insert(row).execute()
        return (res.data or [{}])[0]

    def update_message(self, message_id: str, content: str) -> dict:
        res = (
            self._db.table("workspace_messages")
            .update({"content": content, "edited": True, "updated_at": _now_iso()})
            .eq("id", message_id)
            .execute()
        )
        return (res.data or [{}])[0]

    def delete_message(self, message_id: str) -> dict:
        """Soft-delete a message."""
        res = (
            self._db.table("workspace_messages")
            .update({"deleted": True, "updated_at": _now_iso()})
            .eq("id", message_id)
            .execute()
        )
        return (res.data or [{}])[0]

    def add_reaction(self, message_id: str, emoji: str, user_id: str) -> dict:
        """Toggle a reaction on a message. Returns updated message."""
        # Fetch current reactions
        res = (
            self._db.table("workspace_messages")
            .select("reactions")
            .eq("id", message_id)
            .limit(1)
            .execute()
        )
        msg = (res.data or [{}])[0]
        reactions: list[dict] = msg.get("reactions") or []

        # Find existing reaction with this emoji
        found = False
        for r in reactions:
            if r.get("emoji") == emoji:
                users: list[str] = r.get("users", [])
                if user_id in users:
                    users.remove(user_id)
                    r["count"] = len(users)
                else:
                    users.append(user_id)
                    r["count"] = len(users)
                r["users"] = users
                found = True
                break
        if not found:
            reactions.append({"emoji": emoji, "count": 1, "users": [user_id]})

        # Remove empty reactions
        reactions = [r for r in reactions if r.get("count", 0) > 0]

        res = (
            self._db.table("workspace_messages")
            .update({"reactions": reactions, "updated_at": _now_iso()})
            .eq("id", message_id)
            .execute()
        )
        return (res.data or [{}])[0]

    # -- Read tracking ------------------------------------------------------

    def mark_read(self, channel_id: str, member_type: str, member_id: str) -> None:
        """Update last_read_at for a channel member."""
        (
            self._db.table("workspace_channel_members")
            .update({"last_read_at": _now_iso()})
            .eq("channel_id", channel_id)
            .eq("member_type", member_type)
            .eq("member_id", member_id)
            .execute()
        )

    def get_unread_counts(self, user_id: str) -> dict[str, int]:
        """Return {channel_slug: unread_count} for a user across all channels."""
        # Get all channel memberships for this user
        members_res = (
            self._db.table("workspace_channel_members")
            .select("channel_id, last_read_at")
            .eq("member_type", "user")
            .eq("member_id", user_id)
            .execute()
        )
        memberships = members_res.data or []
        if not memberships:
            return {}

        # Get channel slugs
        channel_ids = [m["channel_id"] for m in memberships]
        channels_res = (
            self._db.table("workspace_channels")
            .select("id, slug")
            .eq("tenant_id", self.tenant_id)
            .in_("id", channel_ids)
            .execute()
        )
        slug_map = {c["id"]: c["slug"] for c in (channels_res.data or [])}

        counts: dict[str, int] = {}
        for m in memberships:
            ch_id = m["channel_id"]
            slug = slug_map.get(ch_id)
            if not slug:
                continue
            last_read = m.get("last_read_at") or "1970-01-01T00:00:00Z"
            # Count messages after last_read
            count_res = (
                self._db.table("workspace_messages")
                .select("id", count="exact")
                .eq("channel_id", ch_id)
                .gt("created_at", last_read)
                .eq("deleted", False)
                .execute()
            )
            cnt = count_res.count if count_res.count is not None else 0
            if cnt > 0:
                counts[slug] = cnt
        return counts

    # -- Members ------------------------------------------------------------

    def add_channel_member(
        self,
        channel_id: str,
        member_type: str,
        member_id: str,
        member_name: str,
        role: str = "member",
    ) -> dict:
        res = (
            self._db.table("workspace_channel_members")
            .upsert(
                {
                    "channel_id": channel_id,
                    "member_type": member_type,
                    "member_id": member_id,
                    "member_name": member_name,
                    "role": role,
                },
                on_conflict="channel_id,member_type,member_id",
            )
            .execute()
        )
        return (res.data or [{}])[0]

    def remove_channel_member(self, channel_id: str, member_type: str, member_id: str) -> None:
        (
            self._db.table("workspace_channel_members")
            .delete()
            .eq("channel_id", channel_id)
            .eq("member_type", member_type)
            .eq("member_id", member_id)
            .execute()
        )

    def list_channel_members(self, channel_id: str) -> list[dict]:
        res = (
            self._db.table("workspace_channel_members")
            .select("*")
            .eq("channel_id", channel_id)
            .order("joined_at")
            .execute()
        )
        return res.data or []

    # -- Seed ---------------------------------------------------------------

    def ensure_default_channels(self, channels_config: list[dict], agents: dict[str, Any]) -> None:
        """Seed workspace_channels from workspace.yml config if they don't exist.

        Args:
            channels_config: List of channel dicts from workspace.yml
                [{"id": "general", "label": "#general", "icon": "Hash", "href": "/workspace/general"}]
            agents: Dict of agent app dicts keyed by app_id
                {"nova": {"name": "Nova", "avatar": "...", "color": "..."}}
        """
        for ch_cfg in channels_config:
            ch_type = ch_cfg.get("type", "chat")
            if ch_type != "chat":
                continue
            slug = ch_cfg.get("id", "")
            if not slug:
                continue
            existing = self.get_channel_by_slug(slug)
            if existing:
                continue
            label = ch_cfg.get("label", slug)
            name = label.lstrip("#")
            icon = ch_cfg.get("icon", "Hash")
            channel = self.create_channel(
                name=name,
                slug=slug,
                icon=icon,
                is_default=True,
                created_by="system",
            )
            ch_id = channel.get("id")
            if not ch_id:
                continue
            # Auto-add backoffice agents to default chat channels.
            # Customer-facing agents (group="customer", e.g. the booking agent)
            # talk to guests on WhatsApp/SMS — they don't belong in the team
            # workspace's #general where humans + backoffice agents coordinate.
            for app_id, app_info in agents.items():
                if app_info.get("group") == "customer":
                    continue
                try:
                    self.add_channel_member(
                        channel_id=ch_id,
                        member_type="agent",
                        member_id=app_id,
                        member_name=app_info.get("name", app_id),
                    )
                except Exception as e:
                    log.warning("Failed to add agent %s to channel %s: %s", app_id, slug, e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
