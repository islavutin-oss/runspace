"""+ 3: ChannelTransport protocol, transport selection, and reply threading round-trip."""

from __future__ import annotations

import asyncio
import json

import pytest

from runspace.ingestion.transport import (  # noqa: E402
    ChannelTransport,
    OutboundReply,
    pick_telegram_transport_mode,
)

# ── transport selection ──────────────────────────────────────────────


def test_transport_mode_default_webhook():
    assert pick_telegram_transport_mode({}, "acme") == "webhook"


def test_transport_mode_workspace_yml_override():
    cfg = {"messaging": {"telegram_bots": [{"name": "default", "transport": "polling"}]}}
    assert pick_telegram_transport_mode(cfg, "acme") == "polling"


def test_transport_mode_env_overrides_workspace(monkeypatch):
    cfg = {"messaging": {"telegram_bots": [{"name": "default", "transport": "webhook"}]}}
    monkeypatch.setenv("TELEGRAM_TRANSPORT_ACME", "polling")
    assert pick_telegram_transport_mode(cfg, "acme") == "polling"


def test_transport_mode_env_tenant_with_dash(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSPORT_ACME_DEMO", "polling")
    assert pick_telegram_transport_mode({}, "acme-demo") == "polling"


def test_transport_mode_unknown_value_falls_through():
    cfg = {"messaging": {"telegram_bots": [{"name": "default", "transport": "rocket-mail"}]}}
    assert pick_telegram_transport_mode(cfg, "acme") == "webhook"


# ── protocol contract ────────────────────────────────────────────────


class _FakeTransport:
    """Hand-rolled impl just to verify the Protocol is satisfied
    structurally (no `isinstance` because Protocols are duck-typed)."""

    provider = "telegram"

    def __init__(self):
        self.started = False
        self.sent: list[OutboundReply] = []

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    async def send(self, reply):
        self.sent.append(reply)


def test_protocol_methods_present():
    t: ChannelTransport = _FakeTransport()  # type-check (mypy/runtime tolerant)
    assert hasattr(t, "start") and callable(t.start)
    assert hasattr(t, "stop") and callable(t.stop)
    assert hasattr(t, "send") and callable(t.send)
    assert t.provider == "telegram"


@pytest.mark.asyncio
async def test_outbound_reply_carries_thread_id():
    t = _FakeTransport()
    await t.send(
        OutboundReply(
            chat_id="-1001",
            text="ok",
            reply_to=42,
            thread_id=7,
        )
    )
    assert t.sent[0].thread_id == 7


# ── reply threading round-trip ───────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_update_threads_reply_on_topic_message(monkeypatch):
    """An inbound with `message_thread_id` must produce an outbound
    reply with the same `message_thread_id` — that's the topic-routing
    contract from.
    """
    from runspace.ingestion import telegram as tg

    captured: dict = {}

    async def fake_send_reply(bot_token, chat_id, text, *, reply_to=None, thread_id=None):
        captured.update(
            bot_token=bot_token, chat_id=chat_id, text=text, reply_to=reply_to, thread_id=thread_id
        )

    async def fake_download(bot_token, file_obj, *, file_kind):
        return b"PDFBYTES", "test.pdf"

    monkeypatch.setattr(tg, "_send_reply", fake_send_reply)
    monkeypatch.setattr(tg, "_download_telegram_file", fake_download)

    # Stub FileStorage so storage.put doesn't try to write to disk.
    class _FakeMeta:
        file_id = "f01"
        original_name = "test.pdf"
        size_bytes = 9
        content_type = "application/pdf"

    class _FakeStorage:
        def put(self, *a, **kw):
            return _FakeMeta()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    from runspace import protocols

    monkeypatch.setattr(protocols, "get_file_storage", lambda: _FakeStorage())

    class _FakeRegistry:
        async def chat(self, agent_id, prompt, session_id):
            return {"text": "Parsed: i07", "tools_used": ["process_invoice"]}

    workspace_cfg = {
        "external_channels": [
            {"provider": "telegram", "chat_id": "-1001", "agent": "accountant"},
        ],
        "messaging": {
            "telegram_bots": [
                {"name": "default", "token": "fake-token"},
            ]
        },
    }
    update = {
        "update_id": 100,
        "message": {
            "message_id": 99,
            "date": 1700000000,
            "chat": {"id": -1001, "type": "supergroup"},
            "from": {"id": 555, "username": "ilya"},
            "message_thread_id": 12345,  # ← topic id
            "document": {
                "file_id": "abc",
                "file_name": "invoice.pdf",
                "mime_type": "application/pdf",
            },
        },
    }
    result = await tg.handle_update(
        tenant_id="acme",
        workspace_cfg=workspace_cfg,
        update=update,
        app_registry=_FakeRegistry(),
    )
    assert result.get("ok") is True
    assert captured.get("thread_id") == 12345, (
        "Reply did not propagate message_thread_id — topic threading broken"
    )


# ── polling transport: end-to-end against a fake getUpdates ──────────


@pytest.mark.asyncio
async def test_polling_consumes_updates_and_persists_offset(tmp_path, monkeypatch):
    """One poll cycle: fake server returns 2 updates; transport hands
    each to the handler and writes the highest update_id+1 to disk.
    A subsequent restart must resume from there.
    """
    import httpx

    from runspace.ingestion.polling import (
        TelegramPollingTransport,
    )

    seen_offsets: list[int | None] = []
    update_batches = [
        # First call: two updates.
        [
            {"update_id": 5, "message": {"message_id": 1, "chat": {"id": 1}}},
            {"update_id": 6, "message": {"message_id": 2, "chat": {"id": 1}}},
        ],
        # Second call: empty (long-poll timeout shape).
        [],
    ]

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_offsets.append(int(request.url.params.get("offset") or 0) or None)
        batch = update_batches.pop(0) if update_batches else []
        return httpx.Response(200, json={"ok": True, "result": batch})

    transport = httpx.MockTransport(_handler)
    received: list[dict] = []

    async def handle(upd):
        received.append(upd)
        return {"ok": True}

    poll = TelegramPollingTransport(
        tenant_id="acme",
        bot_token="t",
        handle=handle,
        offset_dir=tmp_path,
    )
    # Force the transport to use our mock httpx
    poll._client = httpx.AsyncClient(transport=transport)

    # Run the loop a fixed number of times by toggling _running.
    poll._running = True

    async def run_a_couple_of_iterations():
        # Manually drive _loop by stopping after the offset has been written.
        task = asyncio.create_task(poll._loop())
        # Wait until offset file appears OR a couple of seconds elapse.
        for _ in range(30):
            if (tmp_path / ".telegram-offset.json").exists():
                break
            await asyncio.sleep(0.05)
        poll._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await run_a_couple_of_iterations()
    await poll._client.aclose()

    assert len(received) == 2
    assert {u["update_id"] for u in received} == {5, 6}

    # Offset file = max(update_id) + 1 = 7
    state = json.loads((tmp_path / ".telegram-offset.json").read_text())
    assert state["offset"] == 7

    # Restart simulation: a fresh transport reads the persisted offset.
    poll2 = TelegramPollingTransport(
        tenant_id="acme",
        bot_token="t",
        handle=handle,
        offset_dir=tmp_path,
    )
    assert poll2._read_offset() == 7


# ── HTTP 409 Conflict handling (regression for 2026-06-21 incident) ──


@pytest.mark.asyncio
async def test_polling_handles_409_conflict_and_recovers(tmp_path, caplog):
    """A 409 from getUpdates means another process is polling the same
    bot token. The loop must (a) not crash, (b) log the cause loudly
    once, (c) throttle subsequent 409 log lines, (d) expose the
    conflict state via status(), and (e) clear it once recovered.

    Pre-fix behaviour: 409 fell into the generic `except Exception`
    branch, logged the entire URL (including the bot token) every
    backoff cycle for weeks, and gave the admin UI no signal to show.
    """
    import logging

    import httpx

    from runspace.ingestion.polling import TelegramPollingTransport

    # Three poll cycles: 409, 409, success-with-no-updates.
    responses = iter(
        [
            httpx.Response(
                409,
                json={
                    "ok": False,
                    "error_code": 409,
                    "description": "terminated by other getUpdates request",
                },
            ),
            httpx.Response(
                409,
                json={
                    "ok": False,
                    "error_code": 409,
                    "description": "terminated by other getUpdates request",
                },
            ),
            httpx.Response(200, json={"ok": True, "result": []}),
        ]
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        try:
            return next(responses)
        except StopIteration:
            # After we've recovered, keep returning empty 200s.
            return httpx.Response(200, json={"ok": True, "result": []})

    poll = TelegramPollingTransport(
        tenant_id="acme",
        bot_token="t",
        handle=lambda upd: asyncio.sleep(0, result={"ok": True}),
        offset_dir=tmp_path,
    )
    poll._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    # Make the backoff sleeps near-instant so the test runs in ms.
    from runspace.ingestion import polling as polling_mod

    real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        await real_sleep(min(sec, 0.01))

    poll._running = True

    with caplog.at_level(logging.WARNING, logger=polling_mod.log.name):
        # Patch asyncio.sleep inside the module so the loop iterates fast.
        import unittest.mock as mock

        with mock.patch.object(polling_mod.asyncio, "sleep", fast_sleep):
            task = asyncio.create_task(poll._loop())
            # Wait until the loop has consumed all three responses
            # (signalled by conflict_count >= 2 and then back to clear).
            for _ in range(200):
                st = poll.status()
                if st["conflict_count"] >= 2 and not st["conflict"]:
                    break
                await real_sleep(0.01)
            poll._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    await poll._client.aclose()

    # The first 409 logged loudly with a cause hint, not just a URL.
    first_409_logs = [
        r
        for r in caplog.records
        if "HTTP 409 Conflict" in r.getMessage() and "another process is polling" in r.getMessage()
    ]
    assert first_409_logs, (
        "expected an explicit '409 Conflict — another process is polling' log line"
    )

    # The bot token was NOT splatted into the log line (pre-fix did this
    # because raise_for_status's str() includes the full URL).
    assert all(
        "/bot t/" not in r.getMessage() and "token=" not in r.getMessage() for r in caplog.records
    )

    # The recovery line was emitted once the 200 came through. The
    # message includes how many retries the streak held — which proves
    # we counted them all (the streak counter resets to 0 on recovery,
    # so we read the count out of the log line).
    recovery_logs = [
        r.getMessage()
        for r in caplog.records
        if "conflict cleared for tenant acme" in r.getMessage()
    ]
    assert recovery_logs, "expected a 'conflict cleared' log on recovery"
    assert "after 2 retries" in recovery_logs[0]

    # status() reflects the recovered state.
    st = poll.status()
    assert st["conflict"] is False
    assert st["conflict_count"] == 0  # reset on recovery
    assert st["running"] is False


@pytest.mark.asyncio
async def test_polling_throttles_repeated_409_logs(tmp_path, caplog):
    """If 409 persists, we don't want to spam the journal. The first
    one logs at WARNING; further 409s are silent until
    CONFLICT_LOG_INTERVAL_S elapses (5 minutes in prod).
    """
    import logging

    import httpx

    from runspace.ingestion import polling as polling_mod
    from runspace.ingestion.polling import TelegramPollingTransport

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "ok": False,
                "error_code": 409,
                "description": "terminated by other getUpdates request",
            },
        )

    poll = TelegramPollingTransport(
        tenant_id="acme",
        bot_token="t",
        handle=lambda upd: asyncio.sleep(0, result={"ok": True}),
        offset_dir=tmp_path,
    )
    poll._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    poll._running = True

    real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        await real_sleep(min(sec, 0.01))

    with caplog.at_level(logging.WARNING, logger=polling_mod.log.name):
        import unittest.mock as mock

        with mock.patch.object(polling_mod.asyncio, "sleep", fast_sleep):
            task = asyncio.create_task(poll._loop())
            # Let it cycle through ~10 iterations of 409.
            for _ in range(200):
                if poll.status()["conflict_count"] >= 10:
                    break
                await real_sleep(0.01)
            poll._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    await poll._client.aclose()

    # Many conflicts hit, only ONE log line (the first) — throttle works.
    conflict_logs = [r for r in caplog.records if "HTTP 409 Conflict" in r.getMessage()]
    assert len(conflict_logs) == 1, (
        f"expected exactly 1 log line despite {poll.status()['conflict_count']} 409s, "
        f"got {len(conflict_logs)}: {[r.getMessage() for r in conflict_logs]}"
    )
    assert poll.status()["conflict_count"] >= 10
