"""Tests for FileInboxTransport — sandbox-mode Transport behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runspace.protocols.transport import FileInboxTransport, IncomingMessage, Transport


@pytest.fixture
def inbox(tmp_path) -> Path:
    return tmp_path


@pytest.fixture
def transport(inbox) -> FileInboxTransport:
    return FileInboxTransport(inbox)


def test_implements_transport_protocol(transport):
    assert isinstance(transport, Transport)


@pytest.mark.asyncio
async def test_replays_one_message_per_envelope(transport, inbox):
    (inbox / "001-acme.json").write_text(
        json.dumps(
            {
                "chat_id": "ops",
                "sender": "Anton",
                "sender_role": "operator",
                "text": "Due 29.04",
                "ts": "2026-04-30T19:01:00+00:00",
                "attachments": [
                    {
                        "file_id": "001-acme.pdf",
                        "filename": "Acme_Supplies_Ltd.pdf",
                        "mime": "application/pdf",
                        "size": 553900,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (inbox / "001-acme.pdf").write_bytes(b"%PDF-1.4 fake content")

    received: list[IncomingMessage] = []

    async def cb(msg: IncomingMessage) -> None:
        received.append(msg)

    transport.on_message(cb)
    await transport.start()

    assert len(received) == 1
    msg = received[0]
    assert msg.transport == "file-inbox"
    assert msg.chat_id == "ops"
    assert msg.sender == "Anton"
    assert msg.sender_role == "operator"
    assert msg.text == "Due 29.04"
    assert len(msg.attachments) == 1
    assert msg.attachments[0].filename == "Acme_Supplies_Ltd.pdf"


@pytest.mark.asyncio
async def test_replays_envelopes_in_order(transport, inbox):
    for i, who in enumerate(["Anton", "Nikita", "Anton"], 1):
        (inbox / f"{i:03d}.json").write_text(
            json.dumps(
                {
                    "chat_id": "ops",
                    "sender": who,
                    "sender_role": "operator",
                    "text": f"msg {i}",
                }
            ),
            encoding="utf-8",
        )
    received: list[IncomingMessage] = []
    transport.on_message(lambda m: _async_append(received, m))
    await transport.start()
    assert [m.sender for m in received] == ["Anton", "Nikita", "Anton"]


@pytest.mark.asyncio
async def test_fan_out_to_multiple_callbacks(transport, inbox):
    (inbox / "001.json").write_text(
        json.dumps(
            {
                "chat_id": "ops",
                "sender": "X",
                "sender_role": "user",
                "text": "y",
            }
        ),
        encoding="utf-8",
    )
    a, b = [], []
    transport.on_message(lambda m: _async_append(a, m))
    transport.on_message(lambda m: _async_append(b, m))
    await transport.start()
    assert len(a) == 1 and len(b) == 1


@pytest.mark.asyncio
async def test_skips_invalid_envelopes(transport, inbox):
    (inbox / "001.json").write_text("not json {{{", encoding="utf-8")
    (inbox / "002.json").write_text(
        json.dumps(
            {
                "chat_id": "ops",
                "sender": "X",
                "sender_role": "user",
                "text": "y",
            }
        ),
        encoding="utf-8",
    )
    received = []
    transport.on_message(lambda m: _async_append(received, m))
    await transport.start()
    assert len(received) == 1


@pytest.mark.asyncio
async def test_empty_inbox_is_no_op(transport):
    transport.on_message(lambda m: _async_append([], m))
    await transport.start()  # must not raise


@pytest.mark.asyncio
async def test_fetch_file_returns_bytes(transport, inbox):
    (inbox / "x.pdf").write_bytes(b"hello world")
    out = await transport.fetch_file("x.pdf")
    assert out == b"hello world"


@pytest.mark.asyncio
async def test_fetch_file_missing_raises(transport):
    with pytest.raises(FileNotFoundError):
        await transport.fetch_file("missing.pdf")


# ── helpers ─────────────────────────────────────────────────────────────
async def _async_append(lst, msg):
    lst.append(msg)
