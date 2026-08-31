"""Disconnect-survival tests for /chat/stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from runspace.workspace.backend.app_registry import AgentApp
from runspace.workspace.backend.gateway import WorkspaceGateway


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _build_gw_with_slow_stream():
    """Gateway whose registry yields a tool_call, sleeps, then yields the
    final response — simulating an agent that takes >100ms to finish."""
    gw = WorkspaceGateway(name="Test")
    gw.registry.default_provider = {"base_url": "http://x", "api_key": "k", "provider": "openai"}
    app = AgentApp(id="dash", name="Tonya", role="Co-pilot", type="agentino", soul_path=None)
    gw.registry.register(app)

    async def fake_chat_stream(app_id: str, message: str, session_id: str) -> AsyncIterator[dict]:
        # 1. tool_call right away — the user-message persistence has
        #    already happened in the runtime layer (see
        #    test_chat_persistence_and_attachments.py).
        gw.registry._add_to_history(session_id, "user", message)
        yield {"type": "tool_call", "name": "shell"}
        # 2. pretend the agent is doing real work for 250ms
        await asyncio.sleep(0.25)
        # 3. final response — and persist to history (this is what
        #    runtimes/agentino.py:stream does after the inner loop).
        text = "Done — ran the benchmark."
        gw.registry._add_to_history(session_id, "assistant", text)
        yield {"type": "response", "text": text, "tools_used": ["shell"]}

    gw.registry.chat_stream = fake_chat_stream  # type: ignore[assignment]
    return gw


class TestChatStreamSurvivesDisconnect:
    def test_inflight_tasks_set_initialized(self):
        """Strong-ref set must exist on the gateway from construction."""
        gw = WorkspaceGateway(name="Test")
        assert hasattr(gw, "_inflight_chat_tasks")
        assert isinstance(gw._inflight_chat_tasks, set)
        assert len(gw._inflight_chat_tasks) == 0

    def test_history_complete_after_full_drain(self):
        """Sanity baseline: a fully-consumed stream persists both turns
        to history."""

        async def go():
            gw = _build_gw_with_slow_stream()
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            app = FastAPI()
            app.include_router(gw.router)

            with TestClient(app) as client:
                # SSE endpoint — TestClient drains the whole stream.
                r = client.post(
                    "/api/workspace/chat/stream",
                    json={"app_id": "dash", "message": "hi", "session_id": "s1"},
                )
                assert r.status_code == 200
                # body has data: lines
                lines = [ln for ln in r.text.splitlines() if ln.startswith("data: ")]
                assert any("tool_call" in ln for ln in lines)
                assert any("response" in ln for ln in lines)

            # Wait for any still-running task to settle (should be none
            # in the full-drain case).
            for _ in range(50):
                if not gw._inflight_chat_tasks:
                    break
                await asyncio.sleep(0.01)

            history = gw.registry._get_history("s1")
            roles = [m["role"] for m in history]
            assert "user" in roles
            assert "assistant" in roles
            texts = {m["role"]: m["content"] for m in history}
            assert "Done" in texts["assistant"]

        _run(go())

    def test_history_persists_even_when_consumer_aborts_early(self):
        """The contract: cancel the SSE consumer mid-flight; the
        background task must still complete and persist the assistant
        reply. Simulates the SPA's tab-switch unmount."""

        async def go():
            gw = _build_gw_with_slow_stream()
            # Drive the event_stream generator directly so we can stop
            # consuming partway. This bypasses the HTTP layer (TestClient
            # always drains) but exercises the exact code path under test.
            target = next(
                (
                    r
                    for r in gw.router.routes
                    if getattr(r, "path", "") == "/api/workspace/chat/stream"
                ),
                None,
            )
            assert target is not None, "chat/stream route not found"
            handler = target.endpoint

            from runspace.workspace.backend.gateway import ChatRequest

            body = ChatRequest(app_id="dash", message="hi", session_id="s2")
            response = await handler(body)
            stream_gen = response.body_iterator

            # Read the first event (tool_call) then stop. Mirrors the
            # browser closing the SSE connection.
            first = await stream_gen.__anext__()
            # body_iterator yields strings (StreamingResponse default)
            assert "tool_call" in (first.decode() if isinstance(first, bytes) else first)

            # Drop the generator without exhausting it — task should
            # detach and keep running. aclose() simulates Starlette
            # cleanup when the client disconnects.
            await stream_gen.aclose()

            # The background task should still be in the registry,
            # because it hasn't completed yet (slow_stream sleeps 250ms).
            assert len(gw._inflight_chat_tasks) >= 1, (
                "background chat task must be strong-referenced after generator close"
            )

            # Wait long enough for the agent loop to finish (250ms +
            # buffer). Then assert history has the assistant reply.
            for _ in range(80):
                if not gw._inflight_chat_tasks:
                    break
                await asyncio.sleep(0.05)
            assert len(gw._inflight_chat_tasks) == 0, "task did not complete"

            history = gw.registry._get_history("s2")
            roles = [m["role"] for m in history]
            assert "user" in roles, f"user message lost (roles={roles})"
            assert "assistant" in roles, (
                f"assistant reply lost — disconnect cancelled the agent run (roles={roles})"
            )

        _run(go())

    def test_strong_ref_prevents_gc(self):
        """Even with no other reference to the task, it must survive."""

        async def go():
            gw = _build_gw_with_slow_stream()
            target = next(
                r
                for r in gw.router.routes
                if getattr(r, "path", "") == "/api/workspace/chat/stream"
            )

            from runspace.workspace.backend.gateway import ChatRequest

            body = ChatRequest(app_id="dash", message="hi", session_id="s3")
            response = await target.endpoint(body)
            gen = response.body_iterator

            # Read first event then drop the generator entirely. Force a
            # GC pass to evict any weakly-referenced asyncio.Task.
            await gen.__anext__()
            await gen.aclose()
            del gen, response
            import gc

            gc.collect()

            # Now wait — the task must still finish.
            for _ in range(80):
                if not gw._inflight_chat_tasks:
                    break
                await asyncio.sleep(0.05)
            assert len(gw._inflight_chat_tasks) == 0
            assert any(m["role"] == "assistant" for m in gw.registry._get_history("s3"))

        _run(go())
