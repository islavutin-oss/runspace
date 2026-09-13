"""A channel is a shared surface; not every caller may write to it.

One post is visible to everyone who opens the workspace, and it persists. That
is right for the people who run the workspace and wrong for a public seat,
where a visitor would be writing into what the next visitor reads.

The gate is keyed on the caller's role and enforced at the endpoint, because
the endpoint is reachable without the UI that hides the composer.
"""

from __future__ import annotations

import pytest

from runspace.workspace.backend.caller import caller_role
from runspace.workspace.backend.gateway import _may_post


@pytest.fixture(autouse=True)
def _clean_role():
    token = caller_role.set(None)
    yield
    caller_role.reset(token)


def test_a_channel_without_post_roles_is_open():
    """Every workspace that predates this setting keeps working."""
    assert _may_post({"id": "general"}, "demo") is True
    assert _may_post({"id": "general"}, None) is True
    assert _may_post(None, "demo") is True


def test_post_roles_admits_the_named_role():
    assert _may_post({"post_roles": ["owner"]}, "owner") is True


def test_post_roles_refuses_everyone_else():
    assert _may_post({"post_roles": ["owner"]}, "demo") is False


def test_an_absent_role_is_the_least_privileged_one():
    """A host that does not distinguish callers must not get write access to a
    channel that named who may write."""
    assert _may_post({"post_roles": ["owner"]}, None) is False


def test_an_empty_list_does_not_silently_lock_everyone_out():
    """`post_roles: []` reads as a misconfiguration, not as "nobody". Treating
    it as a lock would take a live channel read-only on a stray edit."""
    assert _may_post({"post_roles": []}, "demo") is True


def test_roles_compare_as_strings():
    """workspace.yml is YAML: an unquoted value may arrive as a non-string."""
    assert _may_post({"post_roles": [1]}, "1") is True
