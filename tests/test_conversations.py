"""Conversations protocol — contract tests.

Run against `InMemoryConversations` (no I/O, runs anywhere). The same
suite is the contract every backend must satisfy — point it at
`SupabaseConversations` behind a DB-env guard to validate that adapter.
"""

from __future__ import annotations

import pytest

from runspace.protocols.conversations import (
    Conversations,
    InMemoryConversations,
    thread_key,
)


def _conv() -> InMemoryConversations:
    return InMemoryConversations()


def test_is_a_conversations_impl():
    assert isinstance(_conv(), Conversations)


def test_post_and_thread_roundtrip():
    c = _conv()
    c.post(tenant="", party_a="buyer1", party_b="dec1", sender="a", body="hi", sender_name="Buyer")
    c.post(
        tenant="",
        party_a="buyer1",
        party_b="dec1",
        sender="b",
        body="hello",
        sender_name="Seller",
    )
    msgs = c.thread(tenant="", party_a="buyer1", party_b="dec1")
    assert [m.body for m in msgs] == ["hi", "hello"]
    assert [m.sender for m in msgs] == ["a", "b"]


def test_threads_are_isolated_per_pair():
    c = _conv()
    c.post(tenant="", party_a="buyer1", party_b="dec1", sender="a", body="to 1")
    c.post(tenant="", party_a="buyer1", party_b="dec2", sender="a", body="to 2")
    assert len(c.thread(tenant="", party_a="buyer1", party_b="dec1")) == 1
    assert len(c.thread(tenant="", party_a="buyer1", party_b="dec2")) == 1


def test_unknown_thread_is_empty():
    assert _conv().thread(tenant="", party_a="x", party_b="y") == []


def test_list_threads_by_role():
    c = _conv()
    c.post(tenant="", party_a="buyer1", party_b="dec1", sender="a", body="m1")
    c.post(tenant="", party_a="buyer1", party_b="dec2", sender="a", body="m2")
    c.post(tenant="", party_a="buyer2", party_b="dec1", sender="a", body="m3")
    # buyer1 (role a) is in two threads
    assert len(c.list_threads(tenant="", party="buyer1", role="a")) == 2
    # dec1 (role b) is in two threads (buyer1 + buyer2)
    assert len(c.list_threads(tenant="", party="dec1", role="b")) == 2


def test_tenant_isolates_threads():
    c = _conv()
    c.post(tenant="t1", party_a="b", party_b="d", sender="a", body="t1 msg")
    c.post(tenant="t2", party_a="b", party_b="d", sender="a", body="t2 msg")
    assert len(c.thread(tenant="t1", party_a="b", party_b="d")) == 1
    assert len(c.list_threads(tenant="t1", party="b", role="a")) == 1
    assert thread_key("t1", "b", "d") != thread_key("t2", "b", "d")


def test_unread_count_and_mark_read():
    c = _conv()
    c.post(tenant="", party_a="b1", party_b="d1", sender="a", body="hi")
    c.post(tenant="", party_a="b1", party_b="d1", sender="b", body="hello")
    # each side's unread = the message the OTHER side sent
    (t_dec,) = c.list_threads(tenant="", party="d1", role="b")
    (t_buy,) = c.list_threads(tenant="", party="b1", role="a")
    assert t_dec.unread == 1 and t_buy.unread == 1
    # the seller opens the thread → their unread clears
    marked = c.mark_read(tenant="", party_a="b1", party_b="d1", reader_role="b")
    assert marked == 1
    (t_dec2,) = c.list_threads(tenant="", party="d1", role="b")
    assert t_dec2.unread == 0


def test_rejects_bad_sender():
    with pytest.raises(ValueError):
        _conv().post(tenant="", party_a="a", party_b="b", sender="buyer", body="hi")


def test_rejects_empty_body():
    with pytest.raises(ValueError):
        _conv().post(tenant="", party_a="a", party_b="b", sender="a", body="   ")
