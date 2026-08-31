"""(DM access policy + pairing) and step 5 (inline
button callbacks). Tests use the same shape as
test_external_channels_transport — no real Telegram, monkey-patched
network helpers, just the business logic.
"""

from __future__ import annotations

import json
import time

import pytest

from runspace.ingestion.pairing import (  # noqa: E402
    FilePairingState,
    resolve_allow_list,
    resolve_dm_policy,
)
from runspace.ingestion.transport import (  # noqa: E402
    CallbackHandlerRegistry,
    InlineButton,
    OutboundReply,
)

# ── DM policy resolution ─────────────────────────────────────────────


def test_dm_policy_default_pairing():
    assert resolve_dm_policy({}) == "pairing"


def test_dm_policy_explicit_allowlist():
    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "allowlist"},
            ]
        }
    }
    assert resolve_dm_policy(cfg) == "allowlist"


def test_dm_policy_unknown_falls_back_to_disabled():
    """Fail-safe: unknown values lock down DMs rather than opening them up."""
    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "rocket"},
            ]
        }
    }
    assert resolve_dm_policy(cfg) == "disabled"


def test_allow_list_normalises_prefixes():
    cfg = {
        "messaging": {
            "telegram_bots": [
                {
                    "name": "default",
                    "allowFrom": ["telegram:111", "tg:222", "333", "TELEGRAM:444"],
                }
            ]
        }
    }
    assert resolve_allow_list(cfg) == {"111", "222", "333", "444"}


def test_dm_policy_ignores_legacy_channels_shape():
    """`channels.<provider>` (OpenClaw's word) is NOT read here; in
    this codebase `channels:` is the UI sidebar list. Messaging-channel
    config lives under `messaging.telegram_bots` only. Anything
    declared under `channels.telegram` is silently ignored — the
    resolver falls back to the default."""
    cfg = {"channels": {"telegram": {"dmPolicy": "open"}}}
    assert resolve_dm_policy(cfg) == "pairing"  # default, not "open"


def test_dm_policy_ignores_legacy_messaging_telegram_dict_shape():
    """Single-bot `messaging.telegram: {dict}` was an interim
    shape during/21 development; removes it in favour
    of canonical multi-bot list. Anything under that key is
    silently ignored — caller MUST migrate to `telegram_bots: [...]`.
    Resolver returns default policy when nothing canonical is set."""
    cfg = {"messaging": {"telegram": {"dmPolicy": "open", "dmAgent": "accountant"}}}
    assert resolve_dm_policy(cfg) == "pairing"  # default, ignored


# ── pairing store ────────────────────────────────────────────────────


def test_pairing_request_returns_unique_codes(tmp_path):
    store = FilePairingState(tmp_path / ".pairings.json")
    code1 = store.request(sender_id="100", sender_handle="alice", chat_id="-1")
    code2 = store.request(sender_id="200", sender_handle="bob", chat_id="-1")
    assert code1 != code2
    assert len(code1) == 6 and code1.isupper() or code1.isalnum()


def test_pairing_request_idempotent_per_sender(tmp_path):
    """Re-requesting from the same sender returns the same code so
    the owner's UI doesn't get spammed with duplicates."""
    store = FilePairingState(tmp_path / ".pairings.json")
    code1 = store.request(sender_id="100", sender_handle="alice", chat_id="-1")
    code2 = store.request(sender_id="100", sender_handle="alice", chat_id="-1")
    assert code1 == code2


def test_pairing_approve_authorises_sender(tmp_path):
    store = FilePairingState(tmp_path / ".pairings.json")
    code = store.request(sender_id="100", sender_handle="alice", chat_id="-1")
    assert store.is_authorized("100") is False
    rec = store.approve(code)
    assert rec is not None
    assert rec["sender_id"] == "100"
    assert store.is_authorized("100") is True


def test_pairing_approve_unknown_code(tmp_path):
    store = FilePairingState(tmp_path / ".pairings.json")
    assert store.approve("ABCDEF") is None


def test_pairing_approve_expired_code_drops_authorization(tmp_path):
    """An expired pending → approve returns None and does NOT
    authorise. We simulate expiry by hand-editing the json file."""
    store = FilePairingState(tmp_path / ".pairings.json")
    code = store.request(sender_id="100", sender_handle="alice", chat_id="-1")

    # Force the pending entry's expires_at into the past.
    p = tmp_path / ".pairings.json"
    data = json.loads(p.read_text())
    data["pending"][code]["expires_at"] = "2020-01-01T00:00:00+00:00"
    p.write_text(json.dumps(data))

    assert store.approve(code) is None
    assert store.is_authorized("100") is False


def test_pairing_revoke_removes_authorization(tmp_path):
    store = FilePairingState(tmp_path / ".pairings.json")
    code = store.request(sender_id="100", sender_handle="alice", chat_id="-1")
    store.approve(code)
    assert store.is_authorized("100") is True

    assert store.revoke("100") is True
    assert store.is_authorized("100") is False
    # Idempotent: revoking again is a no-op + returns False.
    assert store.revoke("100") is False


def test_pairing_list_pending_omits_expired(tmp_path):
    store = FilePairingState(tmp_path / ".pairings.json")
    store.request(sender_id="100", sender_handle="alice", chat_id="-1")
    store.request(sender_id="200", sender_handle="bob", chat_id="-1")

    p = tmp_path / ".pairings.json"
    data = json.loads(p.read_text())
    # Expire the alice entry.
    for code, rec in data["pending"].items():
        if rec["sender_id"] == "100":
            data["pending"][code]["expires_at"] = "2020-01-01T00:00:00+00:00"
    p.write_text(json.dumps(data))

    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0]["sender_id"] == "200"


# ── DM gate inside handle_update ─────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_update_dm_disabled_returns_silent_ignore(monkeypatch):
    """policy=disabled — even valid DMs are silently dropped."""
    from runspace.ingestion import telegram as tg

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "disabled", "token": "fake"},
            ]
        },
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": "/nonexistent",
    }
    update = {
        "message": {
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "alice"},
            "text": "hi",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=None,
    )
    assert res.get("ignored") == "dm_disabled"


@pytest.mark.asyncio
async def test_handle_update_dm_pairing_creates_code(tmp_path, monkeypatch):
    """Unauthorised DM in pairing mode → store creates a pending,
    bot replies with the code."""
    from runspace.ingestion import telegram as tg

    sent: list[dict] = []

    async def fake_send(bot_token, chat_id, text, *, reply_to=None, thread_id=None, buttons=None):
        sent.append({"chat_id": chat_id, "text": text})

    monkeypatch.setattr(tg, "_send_reply", fake_send)

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "pairing", "token": "fake"},
            ]
        },
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": str(tmp_path),
        "external_channels": [],  # no group bindings
    }
    update = {
        "message": {
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "alice"},
            "text": "hi",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=None,
    )
    assert res.get("pending_pairing") is True
    assert "code" in res

    # Code persisted on disk in the bot-namespaced file (bot name = default)
    pairings = json.loads((tmp_path / ".pairings-default.json").read_text())
    assert len(pairings.get("pending") or {}) == 1

    # Bot replied once with the code in the body
    assert len(sent) == 1
    assert res["code"] in sent[0]["text"]


@pytest.mark.asyncio
async def test_handle_update_dm_pairing_no_agent_configured(tmp_path):
    """Authorised sender with NO `dmAgent` set → fail-soft: drop the
    DM, log a warning, do NOT call the registry. Avoids dispatching
    DMs to a guessed agent and confusing the user."""
    from runspace.ingestion import telegram as tg

    state = FilePairingState(tmp_path / ".pairings-default.json")
    code = state.request(sender_id="42", sender_handle="alice", chat_id="42")
    state.approve(code)

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "pairing", "token": "fake"},  # no dmAgent
            ]
        },
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": str(tmp_path),
        "external_channels": [],
    }
    update = {
        "message": {
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "alice"},
            "text": "hello again",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=None,
    )
    assert res.get("ignored") == "no_dm_agent_configured"


@pytest.mark.asyncio
async def test_handle_update_dm_routes_to_configured_agent(tmp_path, monkeypatch):
    """Authorised sender + `messaging.telegram.dmAgent: accountant` →
    registry.chat(accountant, text, session_id) called, reply sent
    back to the same chat. Session id encodes tenant + sender so a
    repeat DM keeps history."""
    from unittest.mock import AsyncMock, MagicMock

    from runspace.ingestion import telegram as tg

    state = FilePairingState(tmp_path / ".pairings-default.json")
    code = state.request(sender_id="42", sender_handle="alice", chat_id="42")
    state.approve(code)

    sent: list[dict] = []

    async def fake_send(bot_token, chat_id, text, *, reply_to=None, thread_id=None, buttons=None):
        sent.append({"chat_id": chat_id, "text": text, "reply_to": reply_to})

    monkeypatch.setattr(tg, "_send_reply", fake_send)

    registry = MagicMock()
    registry.chat = AsyncMock(
        return_value={
            "text": "✅ Booked: 4 ppl, Friday 8pm.",
            "tools_used": ["create_booking"],
        }
    )

    # Canonical multi-bot shape. Single bot named `default`.
    cfg = {
        "messaging": {
            "telegram_bots": [
                {
                    "name": "default",
                    "dmPolicy": "pairing",
                    "dmAgent": "accountant",
                    "token": "fake",
                },
            ],
        },
        "_base_dir": str(tmp_path),
        "external_channels": [],
    }
    update = {
        "message": {
            "message_id": 999,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "alice"},
            "text": "book a table for 4 friday at 8",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=registry,
    )
    assert res.get("ok") is True
    assert res["agent"] == "accountant"
    # Session id includes tenant + bot + sender so a follow-up DM
    # lands in the same conversation, and different bots talking to
    # the same Telegram user keep separate histories.
    assert res["session"] == "telegram:acme:default:42"
    # Registry called once with the configured agent + raw text
    registry.chat.assert_called_once()
    args = registry.chat.call_args[0]
    assert args[0] == "accountant"
    assert args[1] == "book a table for 4 friday at 8"
    assert args[2] == "telegram:acme:default:42"
    # Reply was sent back, threaded as a reply to the original
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 42
    assert "Booked" in sent[0]["text"]
    assert sent[0]["reply_to"] == 999


@pytest.mark.asyncio
async def test_handle_update_dm_unsupported_type_drops_with_hint(tmp_path, monkeypatch):
    """Authorised sender sends a sticker/voice/location → drop with
    a short hint. Documents and photos ARE accepted (separate test);
    only truly-unhandleable media types fall here."""
    from unittest.mock import AsyncMock, MagicMock

    from runspace.ingestion import telegram as tg

    state = FilePairingState(tmp_path / ".pairings-default.json")
    code = state.request(sender_id="42", sender_handle="alice", chat_id="42")
    state.approve(code)

    sent: list[dict] = []

    async def fake_send(bot_token, chat_id, text, *, reply_to=None, thread_id=None, buttons=None):
        sent.append({"text": text})

    monkeypatch.setattr(tg, "_send_reply", fake_send)

    registry = MagicMock()
    registry.chat = AsyncMock()

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "pairing", "dmAgent": "accountant", "token": "fake"}
            ],
        },
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": str(tmp_path),
        "external_channels": [],
    }
    update = {
        "message": {
            "message_id": 1000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "alice"},
            "voice": {"file_id": "x", "duration": 3},
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=registry,
    )
    assert res.get("ignored") == "dm_unsupported_type"
    registry.chat.assert_not_called()
    assert len(sent) == 1
    assert "text" in sent[0]["text"].lower()


@pytest.mark.asyncio
async def test_handle_update_dm_document_routes_to_process_invoice(tmp_path, monkeypatch):
    """A paired sender drops a PDF in DM → same flow as a group
    upload: download → FileStorage.put → agent.chat(process_invoice
    prompt) → reply. Pairing approval IS the trust gate; no separate
    trusted_senders allow-list needed for DMs."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from runspace.ingestion import telegram as tg

    state = FilePairingState(tmp_path / ".pairings-default.json")
    code = state.request(sender_id="42", sender_handle="alice", chat_id="42")
    state.approve(code)

    sent: list[dict] = []

    async def fake_send(bot_token, chat_id, text, *, reply_to=None, thread_id=None, buttons=None):
        sent.append({"chat_id": chat_id, "text": text, "thread_id": thread_id})

    async def fake_download(bot_token, file_obj, *, file_kind):
        return (b"%PDF-1.4 fake", "scan.pdf")

    monkeypatch.setattr(tg, "_send_reply", fake_send)
    monkeypatch.setattr(tg, "_download_telegram_file", fake_download)

    registry = MagicMock()
    registry.chat = AsyncMock(
        return_value={
            "text": "✅ Acme_Supplies_Ltd — €103,94, due 2024-06-06",
            "tools_used": ["process_invoice"],
        }
    )

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "pairing", "dmAgent": "accountant", "token": "fake"}
            ],
        },
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": str(tmp_path),
        "external_channels": [],
    }
    update = {
        "message": {
            "message_id": 2000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "alice"},
            "caption": "this is urgent",
            "document": {
                "file_id": "BAADBAAxxxx",
                "file_name": "scan.pdf",
                "mime_type": "application/pdf",
                "file_size": 12345,
            },
        },
    }

    storage = MagicMock()
    meta = MagicMock()
    meta.file_id = "abc12345_scan.pdf"
    storage.put.return_value = meta
    with patch("runspace.protocols.get_file_storage", return_value=storage):
        res = await tg.handle_update(
            tenant_id="acme",
            workspace_cfg=cfg,
            update=update,
            app_registry=registry,
        )

    assert res.get("ok") is True
    assert res["agent"] == "accountant"
    # Storage got the bytes
    storage.put.assert_called_once()
    args = storage.put.call_args[0]
    assert args[0] == "acme"
    assert args[1] == "scan.pdf"
    # Agent invoked with process_invoice prompt + the user's caption
    registry.chat.assert_called_once()
    prompt = registry.chat.call_args[0][1]
    assert "process_invoice" in prompt
    assert "scan.pdf" in prompt
    assert "this is urgent" in prompt
    # Reply landed back in the DM
    assert len(sent) == 1
    assert "Acme_Supplies_Ltd" in sent[0]["text"]
    assert sent[0]["chat_id"] == 42
    # DM threading is None (DMs aren't topic-threaded)
    assert sent[0]["thread_id"] is None


def test_strip_bot_mention():
    """Strips @username prefix (case-insensitive), preserves rest."""
    from runspace.ingestion.telegram import _strip_bot_mention

    assert _strip_bot_mention("@ada_bot what's overdue?", "ada_bot") == "what's overdue?"
    assert _strip_bot_mention("@Ada_Bot what's overdue?", "ada_bot") == "what's overdue?"
    assert _strip_bot_mention("  @ada_bot help", "ada_bot") == "help"
    # No leading mention → returned trimmed unchanged
    assert _strip_bot_mention("hi @ada_bot help", "ada_bot") == "hi @ada_bot help"
    # Empty username → just trim
    assert _strip_bot_mention("  hi  ", "") == "hi"


def test_message_addresses_bot_via_mention():
    """A `mention` entity matching @<botname> counts as tagging."""
    from runspace.ingestion.telegram import _message_addresses_bot

    identity = {"id": 8688860483, "username": "ada_bot"}
    msg = {
        "text": "@ada_bot what's overdue?",
        "entities": [{"type": "mention", "offset": 0, "length": len("@ada_bot")}],
    }
    assert _message_addresses_bot(msg, identity) is True


def test_message_addresses_bot_via_reply():
    """Replying to a bot's message counts as tagging without @-mention."""
    from runspace.ingestion.telegram import _message_addresses_bot

    identity = {"id": 8688860483, "username": "ada_bot"}
    msg = {
        "text": "and overdue ones?",
        "reply_to_message": {"from": {"id": 8688860483, "is_bot": True}},
    }
    assert _message_addresses_bot(msg, identity) is True


def test_message_addresses_bot_via_text_mention():
    """`text_mention` (linked-without-@) for the bot's user id counts."""
    from runspace.ingestion.telegram import _message_addresses_bot

    identity = {"id": 8688860483, "username": "ada_bot"}
    msg = {
        "text": "Ada please summarise",
        "entities": [
            {
                "type": "text_mention",
                "offset": 0,
                "length": len("Ada"),
                "user": {"id": 8688860483, "is_bot": True},
            }
        ],
    }
    assert _message_addresses_bot(msg, identity) is True


def test_message_addresses_bot_negative_cases():
    """Plain text, mentions of other users, replies to other users
    must NOT count — silent buffer-only path."""
    from runspace.ingestion.telegram import _message_addresses_bot

    identity = {"id": 8688860483, "username": "ada_bot"}
    # Plain text, no entities, no reply
    assert _message_addresses_bot({"text": "this is urgent"}, identity) is False
    # Mention of someone else
    msg = {
        "text": "@sam please pay",
        "entities": [{"type": "mention", "offset": 0, "length": len("@sam")}],
    }
    assert _message_addresses_bot(msg, identity) is False
    # Reply to a different user
    msg = {
        "text": "ok thanks",
        "reply_to_message": {"from": {"id": 11111, "is_bot": False}},
    }
    assert _message_addresses_bot(msg, identity) is False
    # bot_command entity is NOT a mention (slash commands need a separate path)
    msg = {
        "text": "/start@ada_bot",
        "entities": [{"type": "bot_command", "offset": 0, "length": 15}],
    }
    assert _message_addresses_bot(msg, identity) is False


@pytest.mark.asyncio
async def test_handle_update_group_text_tagged_routes_to_agent(monkeypatch):
    """Group text that tags the bot via @-mention → agent is invoked,
    reply lands as a threaded reply. Buffer is also updated (so
    surrounding chat keeps accumulating context for any later file)."""
    from unittest.mock import AsyncMock, MagicMock

    from runspace.ingestion import telegram as tg
    from runspace.ingestion.buffer import get_buffer, reset_all

    reset_all()

    sent: list[dict] = []

    async def fake_send(bot_token, chat_id, text, *, reply_to=None, thread_id=None, buttons=None):
        sent.append(
            {"chat_id": chat_id, "text": text, "reply_to": reply_to, "thread_id": thread_id}
        )

    async def fake_identity(cfg, bot_config=None):
        return {"id": 8688860483, "username": "ada_bot"}

    monkeypatch.setattr(tg, "_send_reply", fake_send)
    monkeypatch.setattr(tg, "_get_bot_identity", fake_identity)

    registry = MagicMock()
    registry.chat = AsyncMock(
        return_value={
            "text": "Overdue: 2 invoices totalling €330.",
            "tools_used": ["list_invoices"],
        }
    )

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "token": "fake"},
            ]
        },
        "external_channels": [
            {
                "id": "team-tg",
                "provider": "telegram",
                "chat_id": "-1001234567890",
                "agent": "accountant",
            }
        ],
    }
    update = {
        "message": {
            "message_id": 50,
            "chat": {"id": -1001234567890, "type": "supergroup"},
            "from": {"id": 11111, "username": "sam"},
            "date": int(time.time()),
            "text": "@ada_bot what is overdue?",
            "entities": [{"type": "mention", "offset": 0, "length": len("@ada_bot")}],
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=registry,
    )
    assert res.get("ok") is True
    assert res["agent"] == "accountant"
    assert res["addressed"] is True
    # Group session id (per-bot, chat-scoped, no message_id)
    assert res["session"] == "telegram:acme:default:group:-1001234567890"
    # Agent received the text WITHOUT the @-mention
    args = registry.chat.call_args[0]
    assert args[1] == "what is overdue?"
    # Reply went back into the group
    assert len(sent) == 1
    assert sent[0]["chat_id"] == -1001234567890
    assert "Overdue" in sent[0]["text"]
    # Buffer was also updated (so a later file gets this as context)
    buf = get_buffer("acme", "-1001234567890")
    assert "what is overdue?" in buf.render()


@pytest.mark.asyncio
async def test_handle_update_group_text_untagged_buffers_only(monkeypatch):
    """Group text NOT tagged stays in buffer-only mode — agent never
    runs, no reply sent. The team can chat freely without the bot
    interjecting."""
    from unittest.mock import AsyncMock, MagicMock

    from runspace.ingestion import telegram as tg
    from runspace.ingestion.buffer import get_buffer, reset_all

    reset_all()

    async def fake_identity(cfg, bot_config=None):
        return {"id": 8688860483, "username": "ada_bot"}

    sent: list[dict] = []

    async def fake_send(*a, **kw):
        sent.append(kw)

    monkeypatch.setattr(tg, "_get_bot_identity", fake_identity)
    monkeypatch.setattr(tg, "_send_reply", fake_send)

    registry = MagicMock()
    registry.chat = AsyncMock()

    cfg = {
        # token now lives in messaging.telegram_bots[].token (above)
        "external_channels": [
            {
                "id": "team-tg",
                "provider": "telegram",
                "chat_id": "-1001234567890",
                "agent": "accountant",
            }
        ],
    }
    update = {
        "message": {
            "message_id": 60,
            "chat": {"id": -1001234567890, "type": "supergroup"},
            "from": {"id": 11111, "username": "sam"},
            "date": int(time.time()),
            "text": "this is urgent, pay by friday",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=registry,
    )
    assert res.get("buffered") is True
    assert "addressed" not in res
    registry.chat.assert_not_called()
    assert sent == []
    # But the buffer DID record it
    buf = get_buffer("acme", "-1001234567890")
    assert "urgent" in buf.render()


@pytest.mark.asyncio
async def test_handle_update_dm_photo_routes_to_process_invoice(tmp_path, monkeypatch):
    """Photo uploads pick the largest size variant and route through
    the same pipeline as documents."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from runspace.ingestion import telegram as tg

    state = FilePairingState(tmp_path / ".pairings-default.json")
    code = state.request(sender_id="42", sender_handle="alice", chat_id="42")
    state.approve(code)

    async def fake_send(bot_token, chat_id, text, *, reply_to=None, thread_id=None, buttons=None):
        pass

    async def fake_download(bot_token, file_obj, *, file_kind):
        # Helper picks the largest variant; assert that's what came in.
        assert file_obj["file_size"] == 99999, "should pick largest size"
        return (b"\xff\xd8\xff fake jpg", "telegram_BIG.jpg")

    monkeypatch.setattr(tg, "_send_reply", fake_send)
    monkeypatch.setattr(tg, "_download_telegram_file", fake_download)

    registry = MagicMock()
    registry.chat = AsyncMock(return_value={"text": "ok", "tools_used": []})
    storage = MagicMock()
    storage.put.return_value = MagicMock(file_id="x")

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "pairing", "dmAgent": "accountant", "token": "fake"}
            ],
        },
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": str(tmp_path),
        "external_channels": [],
    }
    update = {
        "message": {
            "message_id": 2001,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "alice"},
            "photo": [
                {"file_id": "small", "file_size": 1234},
                {"file_id": "medium", "file_size": 12345},
                {"file_id": "BIG", "file_size": 99999},
            ],
        },
    }
    with patch("runspace.protocols.get_file_storage", return_value=storage):
        res = await tg.handle_update(
            tenant_id="acme",
            workspace_cfg=cfg,
            update=update,
            app_registry=registry,
        )
    assert res.get("ok") is True


@pytest.mark.asyncio
async def test_handle_update_dm_agent_failure_replies_with_error(tmp_path, monkeypatch):
    """Agent.chat raises → user gets a one-line error reply, response
    has error+detail, no exception bubbles up to the polling loop."""
    from unittest.mock import AsyncMock, MagicMock

    from runspace.ingestion import telegram as tg

    state = FilePairingState(tmp_path / ".pairings-default.json")
    code = state.request(sender_id="42", sender_handle="alice", chat_id="42")
    state.approve(code)

    sent: list[dict] = []

    async def fake_send(bot_token, chat_id, text, *, reply_to=None, thread_id=None, buttons=None):
        sent.append({"text": text})

    monkeypatch.setattr(tg, "_send_reply", fake_send)

    registry = MagicMock()
    registry.chat = AsyncMock(side_effect=RuntimeError("router 502"))

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "pairing", "dmAgent": "accountant", "token": "fake"}
            ],
        },
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": str(tmp_path),
        "external_channels": [],
    }
    update = {
        "message": {
            "message_id": 1001,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "alice"},
            "text": "what's pending today?",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=registry,
    )
    assert res.get("error") == "agent_failed"
    assert "router 502" in res.get("detail", "")
    # User got a friendly error
    assert any("Internal error" in s["text"] for s in sent)


# ── security regressions (DoS, log injection, token-burn) ───────────


@pytest.mark.asyncio
async def test_handle_update_dm_truncates_long_text(tmp_path, monkeypatch):
    """Token-burn defense: a DM with 50k chars is truncated before
    being passed to the agent so a malicious sender can't push the
    LLM into a multi-second response on every message."""
    from unittest.mock import AsyncMock, MagicMock

    from runspace.ingestion import telegram as tg

    state = FilePairingState(tmp_path / ".pairings-default.json")
    code = state.request(sender_id="42", sender_handle="alice", chat_id="42")
    state.approve(code)

    monkeypatch.setattr(tg, "_send_reply", AsyncMock())

    registry = MagicMock()
    registry.chat = AsyncMock(return_value={"text": "ok", "tools_used": []})

    cfg = {
        "messaging": {
            "telegram_bots": [
                {
                    "name": "default",
                    "dmPolicy": "pairing",
                    "token": "fake",
                    "dmAgent": "accountant",
                },
            ]
        },
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": str(tmp_path),
        "external_channels": [],
    }
    update = {
        "message": {
            "message_id": 1,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "alice"},
            "text": "x" * 50_000,
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=registry,
    )
    assert res.get("ok") is True
    # Agent received the truncated form (<= MAX_DM_TEXT_LEN)
    args = registry.chat.call_args[0]
    assert len(args[1]) == tg.MAX_DM_TEXT_LEN


@pytest.mark.asyncio
async def test_pairing_bucket_full_refuses_new_unknown_sender(tmp_path, monkeypatch):
    """DoS defense: when the pending bucket is at the cap, a *new*
    stranger gets silently refused. An *existing* pending sender
    still gets their idempotent reply (so a legit user mid-flow
    isn't blocked by the cap)."""
    from unittest.mock import AsyncMock

    from runspace.ingestion import telegram as tg

    monkeypatch.setattr(tg, "_send_reply", AsyncMock())

    state = FilePairingState(tmp_path / ".pairings-default.json")
    # Fill the bucket up to the cap with synthetic senders.
    for i in range(tg.MAX_PENDING_PAIRINGS):
        state.request(sender_id=str(1000 + i), sender_handle=f"user{i}", chat_id=str(1000 + i))

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "pairing", "token": "fake"},
            ]
        },
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": str(tmp_path),
        "external_channels": [],
    }
    update = {
        "message": {
            "message_id": 2,
            "chat": {"id": 99999, "type": "private"},
            "from": {"id": 99999, "username": "newcomer"},
            "text": "hi",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=None,
    )
    assert res.get("ignored") == "pairing_bucket_full"


def test_safe_strips_control_chars_and_truncates():
    """Log-injection defense: sender_handle with a newline or NUL
    must not be able to forge a fake log line. _safe drops control
    chars and caps length."""
    from runspace.ingestion.telegram import _safe

    out = _safe("alice\n[ERROR] fake log forged\x00", limit=64)
    assert "\n" not in out and "\x00" not in out
    assert "fake log forged" in out  # text content preserved
    assert _safe("a" * 200, limit=10) == "aaaaaaaaaa…"
    assert _safe("") == "<empty>"
    assert _safe(None) == "<empty>"


@pytest.mark.asyncio
async def test_handle_update_records_unbound_chat_to_discovery_file(tmp_path, monkeypatch):
    """When a group message comes in for a chat with no binding,
    telegram.py records the chat in `.discovered-chats-<bot>.json`
    so the owner can bind it via the workspace UI without hunting
    down the chat_id manually."""
    from runspace.ingestion import telegram as tg

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "ada", "token": "fake"},
            ]
        },
        "_base_dir": str(tmp_path),
        "external_channels": [],
    }
    update = {
        "message": {
            "message_id": 7,
            "date": int(time.time()),
            "chat": {"id": -1009999, "type": "supergroup", "title": "Acme Team"},
            "from": {"id": 123456789, "username": "octocat"},
            "text": "anybody home?",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=None,
    )
    assert res.get("ignored") == "unbound chat"
    assert res.get("discovered") is True
    discovery_path = tmp_path / ".discovered-chats-ada.json"
    assert discovery_path.exists()
    data = json.loads(discovery_path.read_text())
    assert "-1009999" in data
    rec = data["-1009999"]
    assert rec["title"] == "Acme Team"
    assert rec["type"] == "supergroup"
    assert rec["count"] == 1
    assert rec["last_sender"] == "@octocat"
    # Second message bumps count, updates last_seen, preserves first_seen
    update["message"]["message_id"] = 8
    await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=None,
    )
    data2 = json.loads(discovery_path.read_text())
    assert data2["-1009999"]["count"] == 2
    assert data2["-1009999"]["first_seen"] == rec["first_seen"]


def test_reload_config_mutates_in_place(tmp_path):
    """`reload_config()` must mutate `_workspace_cfg` IN PLACE so the
    polling task's closure (which captured cfg by reference) sees
    the new state on the next message — no asyncio respawn needed."""
    import yaml as _yaml

    from runspace.workspace.backend.gateway import WorkspaceGateway

    yml = tmp_path / "workspace.yml"
    yml.write_text(
        _yaml.safe_dump(
            {
                "name": "Test",
                "messaging": {
                    "telegram_bots": [
                        {"name": "ada", "token": "tok-a", "dmAgent": "accountant"},
                    ]
                },
                "external_channels": [],
            }
        )
    )
    gw = WorkspaceGateway.from_config(str(yml))
    captured_cfg_id = id(gw._workspace_cfg)
    before_bindings = list(gw._workspace_cfg.get("external_channels") or [])
    assert before_bindings == []

    # Edit the yaml on disk: add a binding + change dmAgent.
    yml.write_text(
        _yaml.safe_dump(
            {
                "name": "Test",
                "messaging": {
                    "telegram_bots": [
                        {"name": "ada", "token": "tok-a", "dmAgent": "booking"},
                    ]
                },
                "external_channels": [
                    {
                        "provider": "telegram",
                        "chat_id": "-1001",
                        "agent": "accountant",
                        "bot": "ada",
                        "id": "x",
                    },
                ],
            }
        )
    )
    diff = gw.reload_config()

    # Same dict identity — closure capture intact.
    assert id(gw._workspace_cfg) == captured_cfg_id
    # New binding visible.
    assert len(gw._workspace_cfg["external_channels"]) == 1
    assert gw._workspace_cfg["external_channels"][0]["chat_id"] == "-1001"
    # dmAgent change visible.
    bots = gw._workspace_cfg["messaging"]["telegram_bots"]
    assert bots[0]["dmAgent"] == "booking"
    # Diff captures the change.
    assert diff["external_channels"]["before"] == 0
    assert diff["external_channels"]["after"] == 1
    assert diff["dmAgent_changes"] == {
        "ada": {"before": "accountant", "after": "booking"},
    }


def test_route_external_channels_add_hot_reloads(tmp_path):
    """End-to-end: POST /api/workspace/external-channels appends a
    binding to workspace.yml AND mutates the in-memory cfg in place.
    No container restart; the next inbound message would see the new
    binding via the polling task's cfg-by-reference closure."""
    import yaml as _yaml
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from runspace.workspace.backend.gateway import WorkspaceGateway

    yml = tmp_path / "workspace.yml"

    def _yaml_dump(d):
        return _yaml.safe_dump(d, sort_keys=False)

    yml.write_text(
        _yaml_dump(
            {
                "name": "Test",
                "messaging": {
                    "telegram_bots": [
                        {"name": "ada", "token": "tok-a", "dmAgent": "accountant"},
                    ]
                },
                "external_channels": [],
            }
        )
    )

    async def fake_admin():
        return {"role": "admin"}

    gw = WorkspaceGateway.from_config(
        str(yml),
        admin_dependency=Depends(fake_admin),
    )
    captured_cfg_id = id(gw._workspace_cfg)
    app = FastAPI()
    app.include_router(gw.router)
    client = TestClient(app)

    body = {
        "id": "team-tg",
        "chat_id": "-1001234567890",
        "agent": "accountant",
        "bot": "ada",
        "trusted_senders": ["123456789"],
    }
    r = client.post("/api/workspace/external-channels", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["binding"]["chat_id"] == "-1001234567890"
    assert data["binding"]["bot"] == "ada"
    assert data["reload"]["external_channels"] == {"before": 0, "after": 1}

    # File was updated
    on_disk = _yaml.safe_load(yml.read_text())
    assert len(on_disk["external_channels"]) == 1
    assert on_disk["external_channels"][0]["chat_id"] == "-1001234567890"

    # In-memory cfg mutated IN PLACE (same dict identity)
    assert id(gw._workspace_cfg) == captured_cfg_id
    assert len(gw._workspace_cfg["external_channels"]) == 1

    # Duplicate (bot, chat_id) is rejected with 409
    r2 = client.post("/api/workspace/external-channels", json=body)
    assert r2.status_code == 409


def test_route_external_channels_unknown_bot_rejected(tmp_path):
    """Binding to a bot that isn't in messaging.telegram_bots → 400.
    Catches typos before they silently land on disk."""
    import yaml as _yaml
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from runspace.workspace.backend.gateway import WorkspaceGateway

    yml = tmp_path / "workspace.yml"
    yml.write_text(
        _yaml.safe_dump(
            {
                "name": "Test",
                "messaging": {
                    "telegram_bots": [
                        {"name": "ada", "token": "tok-a"},
                    ]
                },
                "external_channels": [],
            }
        )
    )

    async def fake_admin():
        return {"role": "admin"}

    gw = WorkspaceGateway.from_config(
        str(yml),
        admin_dependency=Depends(fake_admin),
    )
    app = FastAPI()
    app.include_router(gw.router)
    client = TestClient(app)

    r = client.post(
        "/api/workspace/external-channels",
        json={
            "chat_id": "-1001",
            "agent": "accountant",
            "bot": "max",
        },
    )
    assert r.status_code == 400
    assert "max" in r.text


def test_route_discovered_chats_filters_already_bound(tmp_path):
    """GET /api/workspace/discovered-chats returns only chats that
    DON'T have a binding yet — once you bind one, it disappears
    from the list (the UI now has nothing to suggest for it)."""
    import yaml as _yaml
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runspace.workspace.backend.gateway import WorkspaceGateway

    yml = tmp_path / "workspace.yml"
    yml.write_text(
        _yaml.safe_dump(
            {
                "name": "Test",
                "messaging": {
                    "telegram_bots": [
                        {"name": "ada", "token": "tok-a"},
                    ]
                },
                "external_channels": [
                    {
                        "provider": "telegram",
                        "chat_id": "-1001",
                        "bot": "ada",
                        "agent": "accountant",
                        "id": "already-bound",
                    },
                ],
            }
        )
    )
    # Seed two discovered chats: one already bound, one not.
    (tmp_path / ".discovered-chats-ada.json").write_text(
        json.dumps(
            {
                "-1001": {
                    "type": "supergroup",
                    "title": "Already Bound",
                    "first_seen": "2026-05-05T10:00:00+00:00",
                    "last_seen": "2026-05-05T10:05:00+00:00",
                    "count": 3,
                    "last_sender": "@sam",
                },
                "-1002": {
                    "type": "supergroup",
                    "title": "Pending Binding",
                    "first_seen": "2026-05-05T11:00:00+00:00",
                    "last_seen": "2026-05-05T11:05:00+00:00",
                    "count": 1,
                    "last_sender": "@sam",
                },
            }
        )
    )

    gw = WorkspaceGateway.from_config(str(yml))
    app = FastAPI()
    app.include_router(gw.router)
    client = TestClient(app)

    r = client.get("/api/workspace/discovered-chats")
    assert r.status_code == 200
    data = r.json()
    assert data["bots"] == ["ada"]
    pending = data["by_bot"]["ada"]
    # The bound chat is filtered out
    assert len(pending) == 1
    assert pending[0]["chat_id"] == "-1002"
    assert pending[0]["title"] == "Pending Binding"


def test_route_add_telegram_bot_creates_entry(tmp_path):
    """POST /api/workspace/telegram-bots appends to messaging.
    telegram_bots, hot-reloads in place, and rejects duplicates."""
    import yaml as _yaml
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from runspace.workspace.backend.gateway import WorkspaceGateway

    yml = tmp_path / "workspace.yml"
    yml.write_text(
        _yaml.safe_dump(
            {
                "name": "Test",
                "messaging": {
                    "telegram_bots": [
                        {"name": "ada", "token": "${T_ADA}", "dmAgent": "accountant"},
                    ]
                },
                "external_channels": [],
            }
        )
    )

    async def fake_admin():
        return {"role": "admin"}

    gw = WorkspaceGateway.from_config(
        str(yml),
        admin_dependency=Depends(fake_admin),
    )
    app = FastAPI()
    app.include_router(gw.router)
    client = TestClient(app)

    # Happy path
    r = client.post(
        "/api/workspace/telegram-bots",
        json={
            "name": "max",
            "token_ref": "${T_MAX}",
            "dmAgent": "booking",
            "dmPolicy": "open",
            "transport": "polling",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["bot"]["name"] == "max"
    assert data["bot"]["token"] == "${T_MAX}"
    assert data["bot"]["dmPolicy"] == "open"

    # Disk reflects the new bot
    disk = _yaml.safe_load(yml.read_text())
    names = [b["name"] for b in disk["messaging"]["telegram_bots"]]
    assert names == ["ada", "max"]

    # In-memory cfg also reflects it (same dict identity preserved)
    bot_names = [b["name"] for b in gw._workspace_cfg["messaging"]["telegram_bots"]]
    assert bot_names == ["ada", "max"]

    # Duplicate name rejected with 409
    r2 = client.post(
        "/api/workspace/telegram-bots",
        json={
            "name": "max",
            "token_ref": "${T_X}",
        },
    )
    assert r2.status_code == 409


def test_route_add_telegram_bot_rejects_invalid_input(tmp_path):
    """Validation: name slug-shape, dmPolicy enum, transport enum,
    token_ref required."""
    import yaml as _yaml
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from runspace.workspace.backend.gateway import WorkspaceGateway

    yml = tmp_path / "workspace.yml"
    yml.write_text(
        _yaml.safe_dump(
            {
                "name": "Test",
                "messaging": {
                    "telegram_bots": [
                        {"name": "ada", "token": "${T_ADA}"},
                    ]
                },
                "external_channels": [],
            }
        )
    )

    async def fake_admin():
        return {"role": "admin"}

    gw = WorkspaceGateway.from_config(
        str(yml),
        admin_dependency=Depends(fake_admin),
    )
    app = FastAPI()
    app.include_router(gw.router)
    client = TestClient(app)

    # Bad name (uppercase + spaces)
    r = client.post(
        "/api/workspace/telegram-bots",
        json={
            "name": "Max Bot",  # invalid
            "token_ref": "${T_X}",
        },
    )
    assert r.status_code == 400
    assert "name must be" in r.text

    # Empty name
    r = client.post(
        "/api/workspace/telegram-bots",
        json={
            "name": "",
            "token_ref": "${T_X}",
        },
    )
    assert r.status_code == 400

    # Missing token_ref
    r = client.post(
        "/api/workspace/telegram-bots",
        json={
            "name": "max",
        },
    )
    assert r.status_code == 400
    assert "token_ref" in r.text

    # Invalid policy
    r = client.post(
        "/api/workspace/telegram-bots",
        json={
            "name": "max",
            "token_ref": "${T_X}",
            "dmPolicy": "rocket",
        },
    )
    assert r.status_code == 400

    # Invalid transport
    r = client.post(
        "/api/workspace/telegram-bots",
        json={
            "name": "max",
            "token_ref": "${T_X}",
            "transport": "carrier-pigeon",
        },
    )
    assert r.status_code == 400


def test_route_delete_telegram_bot_removes_entry(tmp_path):
    """DELETE /api/workspace/telegram-bots/<name> removes from yml,
    hot-reloads, returns 404 on unknown bot."""
    import yaml as _yaml
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from runspace.workspace.backend.gateway import WorkspaceGateway

    yml = tmp_path / "workspace.yml"
    yml.write_text(
        _yaml.safe_dump(
            {
                "name": "Test",
                "messaging": {
                    "telegram_bots": [
                        {"name": "ada", "token": "${T_ADA}"},
                        {"name": "max", "token": "${T_MAX}"},
                    ]
                },
                "external_channels": [],
            }
        )
    )

    async def fake_admin():
        return {"role": "admin"}

    gw = WorkspaceGateway.from_config(
        str(yml),
        admin_dependency=Depends(fake_admin),
    )
    app = FastAPI()
    app.include_router(gw.router)
    client = TestClient(app)

    # Happy path
    r = client.delete("/api/workspace/telegram-bots/max")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["removed"] == "max"

    disk = _yaml.safe_load(yml.read_text())
    assert [b["name"] for b in disk["messaging"]["telegram_bots"]] == ["ada"]
    # In-memory cfg reflects the removal
    assert [b["name"] for b in gw._workspace_cfg["messaging"]["telegram_bots"]] == ["ada"]

    # 404 on unknown bot
    r2 = client.delete("/api/workspace/telegram-bots/nonexistent")
    assert r2.status_code == 404


def test_reload_agent_evicts_cached_agent(tmp_path):
    """`AppRegistry.reload_agent(agent_id)` clears the lazy-built
    `Agent` instance so the next `chat()` rebuilds it — no container
    restart needed for SOUL.md edits."""
    from runspace.workspace.backend.app_registry import AgentApp, AppRegistry

    reg = AppRegistry(workspace_name="Test", tenant_id="t-x")
    app = AgentApp(
        id="accountant",
        name="Ada",
        role="bookkeeper",
        avatar="📒",
        color="#7C3AED",
        group="backoffice",
        type="agentino",
    )
    reg.register(app)
    # Simulate a chat having warmed the cache.
    app._agent = object()  # sentinel — the real one is an agentino.Agent
    assert reg.reload_agent("accountant") is True
    assert app._agent is None  # next chat will rebuild
    # Calling again on a cold cache returns False but doesn't raise.
    assert reg.reload_agent("accountant") is False
    # Unknown agent returns False, doesn't raise.
    assert reg.reload_agent("nonexistent") is False


def test_pairing_approve_route_enforces_admin_dependency(tmp_path):
    """When the gateway is built with `admin_dependency`, POST to
    approve/revoke must call that dependency. Construct a gateway,
    inject a counting dependency, hit the route via TestClient, and
    confirm it ran. Defense against the regression where the host
    forgets to pass admin_dependency and approve becomes open."""
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from runspace.workspace.backend.gateway import WorkspaceGateway

    calls = {"n": 0}

    async def fake_admin():
        calls["n"] += 1
        return {"role": "admin"}

    gw = WorkspaceGateway(
        name="Test",
        tenant_id="t-pairings",
        admin_dependency=Depends(fake_admin),
    )
    gw._workspace_cfg = {
        "_base_dir": str(tmp_path),
        "messaging": {
            "telegram_bots": [
                {"name": "default", "token": "fake"},
            ]
        },
    }
    # Seed a pending so approve can resolve a real code.
    state = FilePairingState(tmp_path / ".pairings-default.json")
    code = state.request(sender_id="42", sender_handle="alice", chat_id="42")

    app = FastAPI()
    app.include_router(gw.router)
    client = TestClient(app)

    # Approve route hits the dependency before reaching the handler.
    r = client.post(f"/api/workspace/pairings/{code}/approve")
    assert r.status_code == 200, r.text
    assert calls["n"] == 1

    # Revoke route too.
    r2 = client.post("/api/workspace/pairings/42/revoke")
    assert r2.status_code == 200, r2.text
    assert calls["n"] == 2

    # And when the dependency rejects → 403, regardless of code validity.
    async def reject_admin():
        raise HTTPException(403, "not admin")

    gw2 = WorkspaceGateway(
        name="Test2",
        tenant_id="t-pairings",
        admin_dependency=Depends(reject_admin),
    )
    gw2._workspace_cfg = {
        "_base_dir": str(tmp_path),
        "messaging": {
            "telegram_bots": [
                {"name": "default", "token": "fake"},
            ]
        },
    }
    app2 = FastAPI()
    app2.include_router(gw2.router)
    client2 = TestClient(app2)
    r3 = client2.post(f"/api/workspace/pairings/{code}/approve")
    assert r3.status_code == 403


def test_pairing_store_caps_pending_at_hard_cap(tmp_path):
    """Even if the transport layer doesn't refuse, the pairing store
    itself never exceeds MAX_PENDING_PAIRINGS_HARD_CAP entries —
    oldest non-expired is dropped to make room."""
    from runspace.ingestion.pairing import (
        MAX_PENDING_PAIRINGS_HARD_CAP,
        FilePairingState,
    )

    store = FilePairingState(tmp_path / ".pairings-default.json")
    for i in range(MAX_PENDING_PAIRINGS_HARD_CAP + 5):
        store.request(sender_id=str(i), sender_handle=f"u{i}", chat_id=str(i))
    pending = store.list_pending()
    assert len(pending) == MAX_PENDING_PAIRINGS_HARD_CAP


def test_resolve_telegram_bots_legacy_shape_returns_empty():
    """The legacy `messaging.telegram: {dict}` shape is no longer
    accepted — only `messaging.telegram_bots: [...]` is canonical.
    Tenants with the old shape get `[]` and the lifespan early-
    returns, with no polling started. Catches the migration earlier
    than the user-visible "bot doesn't reply" symptom."""
    from runspace.ingestion.pairing import resolve_telegram_bots

    cfg = {
        "messaging": {
            "telegram": {"transport": "polling", "dmPolicy": "pairing", "dmAgent": "accountant"}
        },
    }
    assert resolve_telegram_bots(cfg) == []


def test_resolve_telegram_bots_multi():
    """`messaging.telegram_bots` is the canonical multi-bot shape;
    each bot gets its own namespaced state filenames so they can run
    in parallel without conflicting on disk."""
    from runspace.ingestion.pairing import resolve_telegram_bots

    cfg = {
        "messaging": {
            "telegram_bots": [
                {
                    "name": "ada",
                    "transport": "polling",
                    "dmPolicy": "pairing",
                    "dmAgent": "accountant",
                    "token": "${T_ADA}",
                },
                {
                    "name": "max",
                    "transport": "polling",
                    "dmPolicy": "open",
                    "dmAgent": "booking",
                    "token": "${T_MAX}",
                },
            ],
        },
    }
    bots = resolve_telegram_bots(cfg)
    assert [b["name"] for b in bots] == ["ada", "max"]
    assert bots[0]["pairing_filename"] == ".pairings-ada.json"
    assert bots[0]["offset_filename"] == ".telegram-offset-ada.json"
    assert bots[1]["pairing_filename"] == ".pairings-max.json"
    assert bots[1]["dmPolicy"] == "open"


def test_resolve_telegram_bots_anonymous_entries_skipped():
    """Entries without a `name` are skipped — names are required to
    namespace state files and (later) webhook routes."""
    from runspace.ingestion.pairing import resolve_telegram_bots

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"transport": "polling"},  # no name
                {"name": "max", "transport": "polling"},
            ],
        },
    }
    bots = resolve_telegram_bots(cfg)
    assert len(bots) == 1
    assert bots[0]["name"] == "max"


def test_resolve_dm_policy_per_bot():
    """`resolve_dm_policy(cfg, bot_config)` honours the bot's own
    setting; without bot_config it falls back to first-bot."""
    from runspace.ingestion.pairing import resolve_dm_policy

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "ada", "dmPolicy": "pairing"},
                {"name": "max", "dmPolicy": "open"},
            ],
        },
    }
    assert resolve_dm_policy(cfg) == "pairing"  # first bot
    assert resolve_dm_policy(cfg, {"dmPolicy": "open"}) == "open"
    assert resolve_dm_policy(cfg, {"dmPolicy": "allowlist"}) == "allowlist"


@pytest.mark.asyncio
async def test_handle_update_uses_passed_bot_config(tmp_path, monkeypatch):
    """When the polling transport supplies `bot_config`, all the
    handler's policy/agent/token decisions come from that bot — even
    when workspace.yml has multiple bots configured."""
    from unittest.mock import AsyncMock, MagicMock

    from runspace.ingestion import telegram as tg

    sent: list[dict] = []

    async def fake_send(bot_token, chat_id, text, *, reply_to=None, thread_id=None, buttons=None):
        sent.append({"token": bot_token, "text": text})

    monkeypatch.setattr(tg, "_send_reply", fake_send)

    registry = MagicMock()
    registry.chat = AsyncMock(return_value={"text": "G replied", "tools_used": []})

    cfg = {
        "_base_dir": str(tmp_path),
        "messaging": {
            "telegram_bots": [
                {
                    "name": "ada",
                    "transport": "polling",
                    "dmPolicy": "pairing",
                    "dmAgent": "accountant",
                    "token": "tok-ada",
                    "pairing_filename": ".pairings-ada.json",
                    "offset_filename": ".telegram-offset-ada.json",
                },
                {
                    "name": "max",
                    "transport": "polling",
                    "dmPolicy": "open",
                    "dmAgent": "booking",
                    "token": "tok-max",
                    "pairing_filename": ".pairings-max.json",
                    "offset_filename": ".telegram-offset-max.json",
                },
            ],
        },
        "external_channels": [],
    }
    max_bot = cfg["messaging"]["telegram_bots"][1]

    # Sender is NOT paired anywhere — but max is `open` so the
    # message should sail through and reach the booking agent.
    update = {
        "message": {
            "message_id": 9001,
            "chat": {"id": 88, "type": "private"},
            "from": {"id": 88, "username": "stranger"},
            "text": "book a table for 4 friday",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=registry,
        bot_config=max_bot,
    )
    assert res.get("ok") is True
    assert res["bot"] == "max"
    assert res["agent"] == "booking"
    # Per-bot session id
    assert res["session"] == "telegram:acme:max:88"
    # Token used for reply was max's, not ada's
    assert sent and sent[0]["token"] == "tok-max"


def test_resolve_telegram_settings_first_bot():
    """`resolve_telegram_settings` returns the first bot's dict from
    `messaging.telegram_bots`. UI sidebar `channels:` (a list of
    pages) is unrelated and ignored."""
    from runspace.ingestion.pairing import (
        resolve_telegram_settings,
    )

    cfg = {
        "channels": [{"id": "general"}],  # acme UI list (irrelevant)
        "messaging": {
            "telegram_bots": [
                {
                    "name": "ada",
                    "dmPolicy": "pairing",
                    "dmAgent": "accountant",
                    "transport": "polling",
                },
            ],
        },
    }
    out = resolve_telegram_settings(cfg)
    assert out["name"] == "ada"
    assert out["dmPolicy"] == "pairing"
    assert out["dmAgent"] == "accountant"
    assert out["transport"] == "polling"


@pytest.mark.asyncio
async def test_handle_update_group_message_unaffected_by_dm_policy(monkeypatch):
    """must not regress the group flow. A group/super-
    group inbound still routes through the existing binding gate
    regardless of dmPolicy.
    """
    from runspace.ingestion import telegram as tg

    cfg = {
        "messaging": {
            "telegram_bots": [
                {"name": "default", "dmPolicy": "disabled", "token": "fake"},
            ]
        },
        "external_channels": [
            {"provider": "telegram", "chat_id": "-1001", "agent": "accountant"},
        ],
        # token now lives in messaging.telegram_bots[].token (above)
        "_base_dir": "/nonexistent",
    }
    update = {
        "message": {
            "chat": {"id": -1001, "type": "supergroup"},
            "from": {"id": 42, "username": "alice"},
            "text": "team chat",
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=cfg,
        update=update,
        app_registry=None,
    )
    # Text message in the group → buffered, NOT swallowed by dmPolicy.
    assert res.get("buffered") is True


# ── callback handler registry ────────────────────────────────────────


def test_callback_registry_longest_prefix_wins():
    reg = CallbackHandlerRegistry()

    async def general(_):
        return {"matched": "general"}

    async def specific(_):
        return {"matched": "specific"}

    reg.register("booking:", general)
    reg.register("booking:cancel:", specific)

    handler = reg.lookup("booking:cancel:42")
    assert handler is specific
    handler = reg.lookup("booking:confirm:42")
    assert handler is general


def test_callback_registry_no_match_returns_none():
    reg = CallbackHandlerRegistry()
    assert reg.lookup("anything") is None


@pytest.mark.asyncio
async def test_handle_update_callback_query_routes_through_registry(monkeypatch):
    from runspace.ingestion import telegram as tg

    answered: list[dict] = []

    async def fake_answer(workspace_cfg, callback_id, *, text="", bot_config=None):
        answered.append({"id": callback_id, "text": text})

    monkeypatch.setattr(tg, "_answer_callback", fake_answer)

    reg = CallbackHandlerRegistry()
    captured: list[dict] = []

    async def handler(payload):
        captured.append(payload)
        return {"ack": "Confirmed!"}

    reg.register("booking:confirm:", handler)

    update = {
        "callback_query": {
            "id": "q1",
            "data": "booking:confirm:42",
            "from": {"id": 555},
            "message": {"message_id": 99, "chat": {"id": -1001}},
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg={"providers": {"telegram_bot": {"token": "x"}}},
        update=update,
        app_registry=None,
        callbacks=reg,
    )
    assert res.get("ok") is True
    assert captured[0]["callback_data"] == "booking:confirm:42"
    assert captured[0]["sender_id"] == "555"
    assert answered[0]["text"] == "Confirmed!"


@pytest.mark.asyncio
async def test_handle_update_callback_no_registry_acks_politely(monkeypatch):
    """callback_query without a registry shouldn't crash — politely
    acknowledge so the user's button stops spinning."""
    from runspace.ingestion import telegram as tg

    answered: list[dict] = []

    async def fake_answer(workspace_cfg, callback_id, *, text="", bot_config=None):
        answered.append({"id": callback_id, "text": text})

    monkeypatch.setattr(tg, "_answer_callback", fake_answer)

    update = {
        "callback_query": {
            "id": "q1",
            "data": "x",
            "from": {"id": 1},
            "message": {"message_id": 1, "chat": {"id": 1}},
        },
    }
    res = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg={},
        update=update,
        app_registry=None,
        callbacks=None,
    )
    assert res.get("ignored") == "no_callback_registry"
    assert len(answered) == 1


# ── OutboundReply.buttons round-trip ─────────────────────────────────


def test_outbound_reply_buttons_shape():
    reply = OutboundReply(
        chat_id="42",
        text="Confirm?",
        buttons=[
            [
                InlineButton(label="Yes", callback_data="confirm:42"),
                InlineButton(label="No", callback_data="cancel:42"),
            ],
        ],
    )
    assert reply.buttons[0][0].label == "Yes"
    assert reply.buttons[0][1].callback_data == "cancel:42"
