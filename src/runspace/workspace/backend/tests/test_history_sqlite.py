"""Chat history that survives a restart."""

from __future__ import annotations

import pytest

from runspace.workspace.backend.app_registry import ChatHistoryStore
from runspace.workspace.backend.history_sqlite import (
    SqliteChatHistoryStore,
    history_store_from_env,
)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "history.sqlite"


@pytest.fixture
def store(db):
    return SqliteChatHistoryStore("acme", db_path=db)


def test_history_round_trips_in_order(store):
    store.add("s1", "user", "first")
    store.add("s1", "assistant", "second")
    assert store.get("s1") == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
    ]


def test_history_survives_a_restart(db):
    """The whole point. A new instance is what a process restart looks like."""
    SqliteChatHistoryStore("acme", db_path=db).add("s1", "user", "remember me")
    assert SqliteChatHistoryStore("acme", db_path=db).get("s1") == [
        {"role": "user", "content": "remember me"}
    ]


def test_the_cap_keeps_the_most_recent_not_the_oldest(db):
    """A truncation that kept the head would hand the model the opening of a
    conversation and drop what was just said."""
    s = SqliteChatHistoryStore("acme", db_path=db, max_messages=3)
    for i in range(6):
        s.add("s1", "user", f"m{i}")
    assert [m["content"] for m in s.get("s1")] == ["m3", "m4", "m5"]


def test_one_tenant_cannot_read_another_in_the_same_file(db):
    a = SqliteChatHistoryStore("acme", db_path=db)
    b = SqliteChatHistoryStore("globex", db_path=db)
    a.add("shared-session-id", "user", "acme private")
    assert b.get("shared-session-id") == []


def test_clearing_removes_the_rows_not_just_a_cache(db):
    s = SqliteChatHistoryStore("acme", db_path=db)
    s.add("s1", "user", "hello")
    assert s.clear("s1") is True
    assert SqliteChatHistoryStore("acme", db_path=db).get("s1") == []


def test_clearing_an_unknown_session_reports_nothing_removed(store):
    assert store.clear("never-existed") is False


def test_seeding_is_idempotent(store):
    """A demo is seeded on every deploy. Appending would grow the conversation
    a little more each time until it read as nonsense."""
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    store.seed("demo", msgs)
    store.seed("demo", msgs)
    assert store.get("demo") == msgs


def test_seeding_replaces_rather_than_merges(store):
    store.seed("demo", [{"role": "user", "content": "old"}])
    store.seed("demo", [{"role": "user", "content": "new"}])
    assert store.get("demo") == [{"role": "user", "content": "new"}]


def test_sessions_are_listed_per_tenant(db):
    a = SqliteChatHistoryStore("acme", db_path=db)
    b = SqliteChatHistoryStore("globex", db_path=db)
    a.add("s1", "user", "x")
    a.add("s2", "user", "y")
    b.add("s3", "user", "z")
    assert a.sessions() == ["s1", "s2"]
    assert b.sessions() == ["s3"]


def test_it_satisfies_the_interface_the_registry_expects(store):
    """AppRegistry accepts any ChatHistoryStore. A subclass that dropped a
    method would fail at request time, not at construction."""
    assert isinstance(store, ChatHistoryStore)
    for name in ("get", "add", "clear"):
        assert callable(getattr(store, name))


def test_the_parent_directory_is_created(tmp_path):
    """The default path is `.runspace/history.sqlite`, which does not exist in
    a fresh checkout."""
    s = SqliteChatHistoryStore("acme", db_path=tmp_path / "nested" / "deeper" / "h.sqlite")
    s.add("s1", "user", "x")
    assert (tmp_path / "nested" / "deeper" / "h.sqlite").exists()


def test_the_default_backend_is_unchanged(monkeypatch):
    """Upgrading must not silently move anyone's history onto disk."""
    monkeypatch.delenv("CHAT_HISTORY_BACKEND", raising=False)
    assert type(history_store_from_env("acme")) is ChatHistoryStore


def test_the_env_var_opts_in(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAT_HISTORY_BACKEND", "sqlite")
    monkeypatch.setenv("CHAT_HISTORY_DB", str(tmp_path / "h.sqlite"))
    assert isinstance(history_store_from_env("acme"), SqliteChatHistoryStore)
