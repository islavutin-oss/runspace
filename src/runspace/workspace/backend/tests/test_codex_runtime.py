"""Tests for runtimes/codex.py — mocked-subprocess adapter shape."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest

from runspace.workspace.backend.app_registry import AgentApp, AppRegistry
from runspace.workspace.backend.runtimes import codex as codex_rt


@pytest.fixture(autouse=True)
def _restore_default_event_loop():
    """`asyncio.run` clears the thread-local loop; sibling tests that use
    the deprecated `asyncio.get_event_loop()` rely on one being set. Restore
    a fresh default loop after each test so cross-file ordering stays clean."""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


class _StubProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", *, hang: bool = False):
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self.killed = False
        self.received_stdin: bytes | None = None

    async def communicate(self, stdin: bytes | None = None):
        self.received_stdin = stdin
        if self._hang:
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return 0


def _make_app(workspace_path: str | None = "/tmp/ws-test", model: str | None = None):
    return AgentApp(
        id="codex-bot",
        name="Codex",
        type="codex",
        workspace_path=workspace_path,
        model=model,
    )


def _make_registry():
    return AppRegistry(workspace_name="Test Co", user_name="alice", user_role="owner")


def _jsonl(*events: dict) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode("utf-8")


def test_chat_returns_dict_shape_with_text_and_tools():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl(
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "/bin/bash -lc 'cat note.txt'",
                    "aggregated_output": "hello world",
                    "exit_code": 0,
                },
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Done. Found two files."},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
        )
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        fake_exec.captured_kwargs = kwargs
        return stub

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = asyncio.run(codex_rt.chat(reg, app, "Find files", "sess-1"))

    assert result["text"] == "Done. Found two files."
    assert result["tools_used"] == ["bash"]
    assert result["tool_outputs"] == ["hello world"]
    # cwd was the app's workspace_path
    assert fake_exec.captured_kwargs["cwd"] == "/tmp/ws-test"
    # prompt got fed via stdin
    assert b"Find files" in stub.received_stdin


def test_stream_yields_one_final_response():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
        )
    )

    async def fake_exec(*args, **kwargs):
        return stub

    async def collect():
        events = []
        async for ev in codex_rt.stream(reg, app, "hi", "s"):
            events.append(ev)
        return events

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = asyncio.run(collect())

    assert len(events) == 1
    assert events[0]["type"] == "response"
    assert events[0]["text"] == "ok"


def test_history_is_persisted_across_turns():
    reg = _make_registry()
    app = _make_app()

    def make_stub(reply: str):
        return _StubProc(
            stdout=_jsonl(
                {"type": "item.completed", "item": {"type": "agent_message", "text": reply}}
            )
        )

    stubs = [make_stub("first reply"), make_stub("second reply")]

    async def fake_exec(*args, **kwargs):
        fake_exec.calls.append(stubs[len(fake_exec.calls)])
        return fake_exec.calls[-1]

    fake_exec.calls = []

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(codex_rt.chat(reg, app, "first user msg", "s"))
        asyncio.run(codex_rt.chat(reg, app, "second user msg", "s"))

    # Second prompt fed to subprocess must contain both prior turns
    second_stdin = stubs[1].received_stdin.decode("utf-8")
    assert "first user msg" in second_stdin
    assert "first reply" in second_stdin
    # Registry history advanced
    history = reg._get_history("s")
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_codex_bin_env_overrides_binary():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        return stub

    with patch.dict(os.environ, {"CODEX_BIN": "/opt/custom/codex"}):
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            asyncio.run(codex_rt.chat(reg, app, "hi", "s"))
    assert fake_exec.captured_args[0] == "/opt/custom/codex"


def test_model_flag_passed_when_set():
    reg = _make_registry()
    app = _make_app(model="gpt-5.3-codex")
    stub = _StubProc(
        stdout=_jsonl({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        return stub

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(codex_rt.chat(reg, app, "hi", "s"))
    assert "--model" in fake_exec.captured_args
    assert "gpt-5.3-codex" in fake_exec.captured_args


def test_timeout_returns_error_text_and_kills_process():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(hang=True)

    async def fake_exec(*args, **kwargs):
        return stub

    with patch.object(codex_rt, "DEFAULT_TIMEOUT_S", 0.05):
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = asyncio.run(codex_rt.chat(reg, app, "hi", "s"))

    assert "timed out" in result["text"]
    assert stub.killed is True


def test_build_gate_manager_returns_none():
    reg = _make_registry()
    app = _make_app()
    assert codex_rt.build_gate_manager(reg, app) is None


def test_get_or_create_agent_returns_sentinel():
    reg = _make_registry()
    app = _make_app()
    handle = codex_rt.get_or_create_agent(reg, app)
    assert isinstance(handle, dict)
    assert handle["runtime"] == "codex"
    # Cached on the app
    assert codex_rt.get_or_create_agent(reg, app) is handle
