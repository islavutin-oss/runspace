"""Tests for two related fixes:"""

from __future__ import annotations

import asyncio

from runspace.workspace.backend.app_registry import AgentApp, AppRegistry
from runspace.workspace.backend.gateway import (
    FileAttachmentResponse,
    _ensure_attachments_referenced,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# Bug 2 — user message persists BEFORE agent runs
# ─────────────────────────────────────────────────────────────────────────────


class TestUserMessagePersistedUpfront:
    """Both chat paths (`_chat_agentino` non-streaming and `_stream_agentino`
    streaming) must persist the user message to history BEFORE invoking
    the agent. If the client disconnects mid-flight (tab switch), a
    subsequent /chat/history fetch must show the user's question.
    """

    def _setup(self):
        reg = AppRegistry(workspace_name="Test")
        reg.default_provider = {"base_url": "http://x", "api_key": "k", "provider": "openai"}
        app = AgentApp(id="luca", name="Luca", role="Analyst", type="agentino", group="backoffice")
        app._soul_text = "You are Luca."
        reg.apps["luca"] = app

        # Stub the underlying agent so we can inspect history at the
        # moment the agent is asked to run.
        snapshots: list[list[dict]] = []

        class _StubAgent:
            on_event = None

            async def run(self, msg, session=None):
                # Capture history state at the time agent.run is called
                snapshots.append(list(reg._get_history("s1")))
                return "ok"

            async def stream(self, msg, session=None):
                snapshots.append(list(reg._get_history("s1")))
                # Yield a single TEXT then DONE so the streaming loop completes.
                from agentino.core.message import Event, EventType

                yield Event(type=EventType.TEXT, data="ok")
                yield Event(type=EventType.DONE, data="ok")

        reg._get_or_create_agent = lambda _app: _StubAgent()
        return reg, app, snapshots

    def test_non_streaming_persists_user_before_agent_run(self):
        reg, app, snaps = self._setup()
        _run(reg._chat_agentino(app, "what is the markup?", "s1"))
        # Snapshot at agent.run time must already contain the user message.
        assert snaps and snaps[0]
        assert snaps[0][-1] == {"role": "user", "content": "what is the markup?"}
        # Final history has user + assistant.
        history = reg._get_history("s1")
        roles = [m["role"] for m in history]
        assert roles == ["user", "assistant"]

    def test_streaming_persists_user_before_agent_stream(self):
        reg, app, snaps = self._setup()
        events = _run(_collect_async(reg._stream_agentino(app, "where am I?", "s1")))
        assert snaps and snaps[0]
        assert snaps[0][-1] == {"role": "user", "content": "where am I?"}
        # Final history has user + assistant
        history = reg._get_history("s1")
        roles = [m["role"] for m in history]
        assert roles == ["user", "assistant"]
        # And the response event came through as expected
        assert any(e.get("type") == "response" for e in events)

    def test_user_message_not_passed_twice_to_agent(self):
        """The agent must see the new user message via the `message` arg,
        NOT via the session history. If we add it to history then pass
        history to session, the agent sees the same user turn twice."""
        reg, app, snaps = self._setup()
        # Pre-seed prior turns
        reg._add_to_history("s1", "user", "prior question")
        reg._add_to_history("s1", "assistant", "prior answer")
        _run(reg._chat_agentino(app, "current question", "s1"))
        # snapshots[0] is the history at the moment agent.run was called.
        # It must include the prior turns AND the new user message
        assert snaps[0][-1]["content"] == "current question"


async def _collect_async(it):
    out = []
    async for x in it:
        out.append(x)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Bug 1 — attachment footer fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestEnsureAttachmentsReferenced:
    def test_no_attachments_text_unchanged(self):
        out = _ensure_attachments_referenced("hello", [])
        assert out == "hello"

    def test_response_already_has_url_unchanged(self):
        att = FileAttachmentResponse(
            name="report.pdf",
            url="/api/documents/abc-123/download",
            size=1024,
            type="application/pdf",
        )
        text = "Here you go: [report.pdf](/api/documents/abc-123/download)"
        assert _ensure_attachments_referenced(text, [att]) == text

    def test_response_already_has_filename_unchanged(self):
        """If the agent mentions the filename plainly (without a link),
        we trust it — don't append a duplicate footer.

        Why: many tools return paths like 'Generated report.pdf at /tmp/...'
        and the agent paraphrases that into prose. The filename is the
        signal that they didn't drop the reference entirely."""
        att = FileAttachmentResponse(
            name="report.pdf",
            url="/api/documents/abc-123/download",
            size=1024,
            type="application/pdf",
        )
        text = "I generated report.pdf for you."
        assert _ensure_attachments_referenced(text, [att]) == text

    def test_paraphrased_response_gets_footer(self):
        """The bug: 'I attached the PDF' with neither URL nor filename
        in the text — user has nothing to click. Footer must be added."""
        att = FileAttachmentResponse(
            name="sales-mix-2026-04.pdf",
            url="/api/documents/xyz/download",
            size=2048,
            type="application/pdf",
        )
        text = "Done — I prepared and attached the 1-page PDF."
        out = _ensure_attachments_referenced(text, [att])
        assert text in out
        assert "📎 [sales-mix-2026-04.pdf](/api/documents/xyz/download)" in out

    def test_only_missing_attachments_get_footer(self):
        """When some attachments ARE referenced and others aren't, only
        the missing ones get a footer line — no duplicates for the ones
        the agent already linked."""
        a1 = FileAttachmentResponse(
            name="a.pdf",
            url="/api/documents/A/download",
            size=1,
            type="application/pdf",
        )
        a2 = FileAttachmentResponse(
            name="b.csv",
            url="/api/documents/B/download",
            size=1,
            type="text/csv",
        )
        text = "Here is [a.pdf](/api/documents/A/download). Also attached b."
        out = _ensure_attachments_referenced(text, [a1, a2])
        # a.pdf was already linked — no duplicate
        assert out.count("/api/documents/A/download") == 1
        # b.csv was missing — footer added
        assert "📎 [b.csv](/api/documents/B/download)" in out

    def test_multiple_missing_attachments(self):
        a1 = FileAttachmentResponse(name="a.pdf", url="/u/A", size=1, type="x")
        a2 = FileAttachmentResponse(name="b.csv", url="/u/B", size=1, type="x")
        out = _ensure_attachments_referenced("All ready.", [a1, a2])
        # Both footers present, on separate lines after a blank line
        assert "📎 [a.pdf](/u/A)" in out
        assert "📎 [b.csv](/u/B)" in out
        assert out.startswith("All ready.\n\n")
