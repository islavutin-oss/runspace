"""Tests for runtimes/claude_code.py — mocked-subprocess adapter shape."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest

from runspace.workspace.backend.app_registry import AgentApp, AppRegistry
from runspace.workspace.backend.runtimes import claude_code as cc_rt


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


def _make_app(
    workspace_path: str | None = "/tmp/ws-test",
    model: str | None = None,
    gates_config: dict | None = None,
):
    return AgentApp(
        id="cc-bot",
        name="Claude Code",
        type="claude_code",
        workspace_path=workspace_path,
        model=model,
        gates_config=gates_config,
    )


def _make_registry():
    return AppRegistry(workspace_name="Test Co", user_name="alice", user_role="owner")


def _jsonl(*events: dict) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode("utf-8")


def test_chat_parses_result_event_and_tool_use():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl(
            {"type": "system", "subtype": "init"},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"path": "x.txt"}},
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "content": [{"type": "text", "text": "file body"}]},
                    ]
                },
            },
            {
                "type": "result",
                "result": "I read the file.",
                "total_cost_usd": 0.0042,
                "session_id": "abc-123",
            },
        )
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        fake_exec.captured_kwargs = kwargs
        return stub

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = asyncio.run(cc_rt.chat(reg, app, "read x.txt", "s"))

    assert result["text"] == "I read the file."
    assert result["tools_used"] == ["Read"]
    assert "file body" in result["tool_outputs"][0]
    # cwd matches workspace_path; --add-dir + --permission-mode plan flags present
    assert fake_exec.captured_kwargs["cwd"] == "/tmp/ws-test"
    assert "--add-dir" in fake_exec.captured_args
    assert "--permission-mode" in fake_exec.captured_args
    idx = fake_exec.captured_args.index("--permission-mode")
    assert fake_exec.captured_args[idx + 1] == "plan"


def test_permission_mode_override_via_gates_config():
    reg = _make_registry()
    app = _make_app(gates_config={"cli_permission_mode": "acceptEdits"})
    stub = _StubProc(
        stdout=_jsonl(
            {"type": "result", "result": "ok"},
        )
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        return stub

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(cc_rt.chat(reg, app, "hi", "s"))
    idx = fake_exec.captured_args.index("--permission-mode")
    assert fake_exec.captured_args[idx + 1] == "acceptEdits"


def test_invalid_permission_mode_falls_back_to_plan():
    reg = _make_registry()
    app = _make_app(gates_config={"cli_permission_mode": "wide-open"})
    stub = _StubProc(stdout=_jsonl({"type": "result", "result": "ok"}))

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        return stub

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(cc_rt.chat(reg, app, "hi", "s"))
    idx = fake_exec.captured_args.index("--permission-mode")
    assert fake_exec.captured_args[idx + 1] == "plan"


def test_stream_yields_one_final_response():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(stdout=_jsonl({"type": "result", "result": "ok"}))

    async def fake_exec(*args, **kwargs):
        return stub

    async def collect():
        return [ev async for ev in cc_rt.stream(reg, app, "hi", "s")]

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0]["type"] == "response"


def test_history_persisted_across_turns():
    reg = _make_registry()
    app = _make_app()
    stubs = [
        _StubProc(stdout=_jsonl({"type": "result", "result": "first reply"})),
        _StubProc(stdout=_jsonl({"type": "result", "result": "second reply"})),
    ]

    async def fake_exec(*args, **kwargs):
        return stubs[fake_exec.n]

    fake_exec.n = 0

    def step():
        fake_exec.n += 1

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(cc_rt.chat(reg, app, "first", "s"))
        step()
        asyncio.run(cc_rt.chat(reg, app, "second", "s"))

    second_stdin = stubs[1].received_stdin.decode("utf-8")
    assert "first" in second_stdin
    assert "first reply" in second_stdin


def test_claude_bin_env_override():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(stdout=_jsonl({"type": "result", "result": "ok"}))

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        return stub

    with patch.dict(os.environ, {"CLAUDE_CODE_BIN": "/opt/cc/claude"}):
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            asyncio.run(cc_rt.chat(reg, app, "hi", "s"))
    assert fake_exec.captured_args[0] == "/opt/cc/claude"


def test_timeout_returns_error_text_and_kills():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(hang=True)

    async def fake_exec(*args, **kwargs):
        return stub

    with patch.object(cc_rt, "DEFAULT_TIMEOUT_S", 0.05):
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = asyncio.run(cc_rt.chat(reg, app, "hi", "s"))
    assert "timed out" in result["text"]
    assert stub.killed is True


def test_build_gate_manager_returns_none():
    reg = _make_registry()
    app = _make_app()
    assert cc_rt.build_gate_manager(reg, app) is None
