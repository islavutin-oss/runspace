"""The local messaging backend.

This is the default now, so it carries the channel surface for anyone who has
not wired up Supabase. Tenant isolation matters as much here as in the store:
one file holds every tenant's messages.
"""

from __future__ import annotations

import pytest

from runspace.workspace.backend.messaging_sqlite import SqliteMessagingService


@pytest.fixture
def svc(tmp_path):
    return SqliteMessagingService("acme", db_path=tmp_path / "m.sqlite")


@pytest.fixture
def other(tmp_path):
    """A second tenant sharing the same file."""
    return SqliteMessagingService("globex", db_path=tmp_path / "m.sqlite")


def test_a_fresh_workspace_has_no_channels(svc):
    assert svc.list_channels() == []


def test_creating_a_channel_is_idempotent(svc):
    first = svc.create_channel(name="general", slug="general")
    again = svc.create_channel(name="general", slug="general")
    assert first["id"] == again["id"]
    assert len(svc.list_channels()) == 1


def test_messages_round_trip_in_order(svc):
    ch = svc.create_channel(name="general", slug="general")
    for text in ("first", "second", "third"):
        svc.send_message(ch["id"], "user", "sam", "Sam", text)
    assert [m["content"] for m in svc.get_channel_messages(ch["id"])] == [
        "first",
        "second",
        "third",
    ]


def test_json_columns_come_back_as_structures_not_strings(svc):
    ch = svc.create_channel(name="general", slug="general")
    svc.send_message(
        ch["id"],
        "agent",
        "robin",
        "Robin",
        "done",
        tools_used=["whats_on_the_bench"],
        mentions=["sam"],
        metadata={"turn": 1},
    )
    m = svc.get_channel_messages(ch["id"])[0]
    assert m["tools_used"] == ["whats_on_the_bench"]
    assert m["mentions"] == ["sam"]
    assert m["metadata"] == {"turn": 1}


def test_deleting_a_message_hides_it_without_losing_it(svc):
    ch = svc.create_channel(name="general", slug="general")
    m = svc.send_message(ch["id"], "user", "sam", "Sam", "oops")
    svc.delete_message(m["id"])
    assert svc.get_channel_messages(ch["id"]) == []
    assert len(svc.get_channel_messages(ch["id"], include_deleted=True)) == 1


def test_editing_records_when_it_happened(svc):
    ch = svc.create_channel(name="general", slug="general")
    m = svc.send_message(ch["id"], "user", "sam", "Sam", "draft")
    edited = svc.update_message(m["id"], "final")
    assert edited["content"] == "final"
    assert edited["edited_at"]


def test_a_thread_only_returns_its_own_replies(svc):
    ch = svc.create_channel(name="general", slug="general")
    root = svc.send_message(ch["id"], "user", "sam", "Sam", "question")
    svc.send_message(ch["id"], "agent", "robin", "Robin", "reply", thread_id=root["id"])
    svc.send_message(ch["id"], "user", "sam", "Sam", "unrelated")
    assert [m["content"] for m in svc.get_thread_messages(root["id"])] == ["reply"]


def test_a_reaction_toggles(svc):
    ch = svc.create_channel(name="general", slug="general")
    m = svc.send_message(ch["id"], "user", "sam", "Sam", "nice")
    assert svc.add_reaction(m["id"], "👍", "sam")["reactions"] == {"👍": ["sam"]}
    assert svc.add_reaction(m["id"], "👍", "sam")["reactions"] == {}


def test_unread_counts_ignore_your_own_messages(svc):
    ch = svc.create_channel(name="general", slug="general")
    svc.send_message(ch["id"], "user", "sam", "Sam", "mine")
    assert svc.get_unread_counts("sam") == {}
    svc.send_message(ch["id"], "agent", "robin", "Robin", "theirs")
    assert svc.get_unread_counts("sam") == {ch["id"]: 1}


def test_marking_read_clears_the_count(svc):
    ch = svc.create_channel(name="general", slug="general")
    svc.send_message(ch["id"], "agent", "robin", "Robin", "hello")
    svc.mark_read(ch["id"], "user", "sam")
    svc.get_unread_counts("sam")
    assert svc.get_unread_counts("sam") == {}


def test_members_can_be_added_updated_and_removed(svc):
    ch = svc.create_channel(name="general", slug="general")
    svc.add_channel_member(ch["id"], "agent", "robin", "Robin")
    svc.add_channel_member(ch["id"], "agent", "robin", "Robin", role="owner")
    members = svc.list_channel_members(ch["id"])
    assert len(members) == 1 and members[0]["role"] == "owner"
    svc.remove_channel_member(ch["id"], "agent", "robin")
    assert svc.list_channel_members(ch["id"]) == []


def test_direct_message_channels_are_hidden_by_default(svc):
    svc.create_channel(name="general", slug="general")
    svc.create_channel(name="dm robin", slug="dm-robin")
    assert [c["slug"] for c in svc.list_channels()] == ["general"]
    assert len(svc.list_channels(include_dm=True)) == 2


def test_only_chat_channels_are_seeded(svc):
    svc.ensure_default_channels(
        [
            {"id": "general", "label": "general", "type": "chat"},
            {"id": "workshop", "label": "workshop"},  # chat is the default
            {"id": "activity", "label": "Activity", "type": "activity"},
            {"id": "inbox", "label": "Inbox", "type": "addon"},
        ],
        {"robin": {"name": "Robin"}},
    )
    assert sorted(c["slug"] for c in svc.list_channels()) == ["general", "workshop"]


def test_seeding_puts_every_agent_in_every_channel(svc):
    svc.ensure_default_channels(
        [{"id": "general", "label": "general"}],
        {"robin": {"name": "Robin"}, "vik": {"name": "Vik"}},
    )
    ch = svc.get_channel_by_slug("general")
    assert sorted(m["member_id"] for m in svc.list_channel_members(ch["id"])) == ["robin", "vik"]


def test_one_tenant_cannot_see_another_in_the_same_file(svc, other):
    mine = svc.create_channel(name="general", slug="general")
    svc.send_message(mine["id"], "user", "sam", "Sam", "private")

    assert other.list_channels() == []
    assert other.get_channel_by_slug("general") is None
    # even with the channel id, the rows are scoped to the tenant
    assert other.get_channel_messages(mine["id"]) == []


def test_both_tenants_can_hold_the_same_slug(svc, other):
    a = svc.create_channel(name="general", slug="general")
    b = other.create_channel(name="general", slug="general")
    assert a["id"] != b["id"]


def test_both_backends_expose_the_same_surface():
    """The claim that swapping backends is an environment change only holds if
    the surfaces match. A method added to one and not the other breaks a
    workspace on whichever backend it happens to be using, and the count in
    the docstring was already one behind.
    """
    from runspace.workspace.backend.messaging import MessagingService

    public = lambda cls: {m for m in dir(cls) if not m.startswith("_")}  # noqa: E731
    sqlite, supabase = public(SqliteMessagingService), public(MessagingService)
    assert sqlite == supabase, (
        "the two messaging backends have diverged — "
        f"sqlite only: {sorted(sqlite - supabase)}, supabase only: {sorted(supabase - sqlite)}"
    )
