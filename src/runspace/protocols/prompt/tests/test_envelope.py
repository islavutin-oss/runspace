"""Tests for build_message_envelope — single source of truth for the
runtime meta-prefix."""

from __future__ import annotations

from runspace.protocols.prompt import build_message_envelope


def test_envelope_builds_canonical_meta_line():
    out = build_message_envelope(
        "due today?",
        company="Acme",
        user_name="Ada",
        user_role="Owner",
    )
    assert out == "[company: Acme, user: Ada, role: Owner]\ndue today?"


def test_envelope_omits_meta_line_when_no_metadata():
    """No company/user/role → return message unchanged. Important so
    we don't introduce a leading `[]` line that confuses the model."""
    assert build_message_envelope("hi") == "hi"
    assert build_message_envelope("hi", company=None, user_name="", user_role="") == "hi"


def test_envelope_skips_falsy_parts_individually():
    out = build_message_envelope("x", company="C", user_name=None, user_role="Owner")
    assert out == "[company: C, role: Owner]\nx"


def test_envelope_prepends_memory_block_above_meta():
    out = build_message_envelope(
        "msg",
        company="C",
        user_name="U",
        memory_block="Past memories:\n- thing-1",
    )
    assert out.startswith("Past memories:\n- thing-1\n\n[company: C, user: U]")
    assert out.endswith("msg")


def test_envelope_idempotent_for_same_inputs():
    a = build_message_envelope("m", company="C", user_name="U", user_role="O")
    b = build_message_envelope("m", company="C", user_name="U", user_role="O")
    assert a == b
