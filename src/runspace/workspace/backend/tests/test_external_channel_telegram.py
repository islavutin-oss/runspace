"""Tests for the Telegram external-channel webhook handler ( Phase 1)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from runspace.ingestion.buffer import (  # noqa: E402
    ContextBuffer,
    get_buffer,
    reset_all,
)
from runspace.ingestion.telegram import handle_update  # noqa: E402

# ── ContextBuffer ───────────────────────────────────────────────────────


def test_buffer_holds_messages_and_renders():
    b = ContextBuffer(max_messages=5, window_seconds=60)
    b.push(sender="@sam", text="pay by friday")
    b.push(sender="@ada", text="same supplier as before")
    out = b.render()
    assert "@sam" in out
    assert "@ada" in out
    assert "pay by friday" in out


def test_buffer_drops_messages_outside_window():
    b = ContextBuffer(max_messages=10, window_seconds=60)
    b.push(sender="@x", text="old", ts=time.time() - 600)  # 10 min ago
    b.push(sender="@y", text="recent")
    out = b.render()
    assert "old" not in out
    assert "recent" in out


def test_buffer_caps_at_max_messages():
    b = ContextBuffer(max_messages=3, window_seconds=600)
    for i in range(10):
        b.push(sender="@x", text=f"msg{i}")
    out = b.render()
    # Only last 3 should remain
    assert "msg7" in out and "msg8" in out and "msg9" in out
    assert "msg0" not in out and "msg5" not in out


def test_empty_render_returns_empty_string():
    b = ContextBuffer()
    assert b.render() == ""


def test_buffer_global_registry_returns_same_buffer_per_chat():
    reset_all()
    a1 = get_buffer("t1", "chat1")
    a2 = get_buffer("t1", "chat1")
    a3 = get_buffer("t1", "chat2")
    assert a1 is a2  # same chat → same buffer
    assert a1 is not a3  # different chat → different buffer


# ── handle_update routing ───────────────────────────────────────────────


@pytest.fixture
def workspace_cfg():
    return {
        "tenant_id": "acme",
        "messaging": {
            "telegram_bots": [
                {"name": "default", "token": "fake-bot-token-abc"},
            ]
        },
        "external_channels": [
            {
                "id": "accounting-tg",
                "provider": "telegram",
                "chat_id": "-1001234567890",
                "agent": "accountant",
                "context_window_seconds": 600,
                "context_max_messages": 20,
                "trusted_senders": ["31415926"],
            }
        ],
    }


@pytest.fixture
def mock_registry():
    r = MagicMock()
    r.chat = AsyncMock(
        return_value={
            "text": "✅ Acme_Supplies_Ltd — €103,94, due 2024-06-06",
            "tools_used": ["process_invoice"],
        }
    )
    return r


def _text_update(text: str, sender_id: str = "31415926", sender_handle: str = "sam"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "from": {"id": int(sender_id), "username": sender_handle},
            "chat": {"id": -1001234567890, "type": "supergroup"},
            "date": int(time.time()),
            "text": text,
        },
    }


def _document_update(
    file_id: str = "BAADBAADrwADBREAAQ",
    file_name: str = "scan.pdf",
    sender_id: str = "31415926",
    sender_handle: str = "sam",
):
    return {
        "update_id": 2,
        "message": {
            "message_id": 101,
            "from": {"id": int(sender_id), "username": sender_handle},
            "chat": {"id": -1001234567890, "type": "supergroup"},
            "date": int(time.time()),
            "document": {
                "file_id": file_id,
                "file_name": file_name,
                "mime_type": "application/pdf",
                "file_size": 12345,
            },
        },
    }


@pytest.mark.asyncio
async def test_text_update_buffered_not_processed(workspace_cfg, mock_registry):
    reset_all()
    update = _text_update("pay by friday")
    result = await handle_update(
        tenant_id="acme",
        workspace_cfg=workspace_cfg,
        update=update,
        app_registry=mock_registry,
    )
    assert result.get("buffered") is True
    mock_registry.chat.assert_not_called()
    # Buffer should now contain the message
    buf = get_buffer("acme", "-1001234567890")
    assert "pay" in buf.render()


@pytest.mark.asyncio
async def test_unknown_chat_silently_ignored(workspace_cfg, mock_registry):
    reset_all()
    update = _text_update("hello")
    update["message"]["chat"]["id"] = -9999999999  # unknown
    result = await handle_update(
        tenant_id="acme",
        workspace_cfg=workspace_cfg,
        update=update,
        app_registry=mock_registry,
    )
    assert "ignored" in result
    mock_registry.chat.assert_not_called()


@pytest.mark.asyncio
async def test_document_from_untrusted_sender_dropped(workspace_cfg, mock_registry):
    reset_all()
    update = _document_update(sender_id="999", sender_handle="rando")
    with patch("runspace.ingestion.telegram._download_telegram_file", new_callable=AsyncMock) as dl:
        result = await handle_update(
            tenant_id="acme",
            workspace_cfg=workspace_cfg,
            update=update,
            app_registry=mock_registry,
        )
    assert result.get("ignored") == "untrusted_sender"
    dl.assert_not_called()  # never even attempted download
    mock_registry.chat.assert_not_called()


@pytest.mark.asyncio
async def test_document_from_trusted_sender_processed(workspace_cfg, mock_registry):
    """Happy path: trusted sender drops a PDF, agent gets called with
    the buffered chat as caption, reply goes back to TG."""
    reset_all()
    # Seed the buffer with surrounding chat
    buf = get_buffer("acme", "-1001234567890")
    buf.push(sender="@sam", text="this is urgent, pay by friday")

    update = _document_update()
    fake_pdf_bytes = b"%PDF-1.4 fake content"

    with (
        patch("runspace.ingestion.telegram._download_telegram_file", new_callable=AsyncMock) as dl,
        patch("runspace.ingestion.telegram._send_reply", new_callable=AsyncMock) as send_reply,
        patch("runspace.protocols.get_file_storage") as get_storage,
    ):
        dl.return_value = (fake_pdf_bytes, "scan.pdf")
        storage = MagicMock()
        meta = MagicMock()
        meta.file_id = "abc12345_scan.pdf"
        storage.put.return_value = meta
        get_storage.return_value = storage

        result = await handle_update(
            tenant_id="acme",
            workspace_cfg=workspace_cfg,
            update=update,
            app_registry=mock_registry,
        )

    assert result["ok"] is True
    assert result["agent"] == "accountant"
    # Storage got called with the file
    storage.put.assert_called_once()
    args, kwargs = storage.put.call_args
    assert args[0] == "acme"
    assert args[1] == "scan.pdf"
    assert args[2] == fake_pdf_bytes
    # Agent got called; the prompt should include the buffered context
    mock_registry.chat.assert_called_once()
    call_args = mock_registry.chat.call_args
    prompt = call_args[0][1]
    assert "@sam" in prompt and "urgent" in prompt
    assert "process_invoice" in prompt
    # TG reply was sent
    send_reply.assert_called_once()


@pytest.mark.asyncio
async def test_missing_bot_token_returns_error(workspace_cfg, mock_registry):
    reset_all()
    cfg = dict(workspace_cfg)
    cfg["messaging"] = {
        "telegram_bots": [
            {"name": "default", "token": ""},  # empty token
        ]
    }
    update = _document_update()
    result = await handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=mock_registry,
    )
    assert result.get("error") == "no_bot_token"


@pytest.mark.asyncio
async def test_no_external_channels_unknown_chat_ignored(mock_registry):
    """Tenant has no external_channels declared at all → every update
    is "unbound chat", silently ignored."""
    reset_all()
    update = _text_update("anything")
    result = await handle_update(
        tenant_id="acme",
        workspace_cfg={"providers": {}, "external_channels": []},
        update=update,
        app_registry=mock_registry,
    )
    assert "ignored" in result
    mock_registry.chat.assert_not_called()


@pytest.mark.asyncio
async def test_text_only_processes_no_trusted_check(workspace_cfg, mock_registry):
    """trusted_senders only gates FILE handling. Anyone in the
    chat may emit text and have it buffered (the team has many
    voices; only file-uploaders need to be on the allow-list)."""
    reset_all()
    update = _text_update("random user comment", sender_id="999", sender_handle="rando")
    result = await handle_update(
        tenant_id="acme",
        workspace_cfg=workspace_cfg,
        update=update,
        app_registry=mock_registry,
    )
    assert result.get("buffered") is True
