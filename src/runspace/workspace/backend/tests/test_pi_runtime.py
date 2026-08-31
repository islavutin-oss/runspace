"""Tests for runtimes/pi.py — mocked-subprocess adapter shape.

Mirrors test_codex_runtime.py / test_claude_code_runtime.py — pi is the
fifth runtime adapter on the same shelf (subprocess CLI engines).
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest

from runspace.workspace.backend.app_registry import AgentApp, AppRegistry
from runspace.workspace.backend.runtimes import pi as pi_rt


@pytest.fixture(autouse=True)
def _restore_default_event_loop():
    """`asyncio.run` clears the thread-local loop; sibling tests using the
    deprecated `asyncio.get_event_loop()` rely on one being set. Restore."""
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
    workspace_path: str | None = "/tmp/ws-pi-test",
    model: str | None = None,
    gates_config: dict | None = None,
):
    return AgentApp(
        id="pi-bot",
        name="Pi",
        type="pi",
        workspace_path=workspace_path,
        model=model,
        gates_config=gates_config,
    )


def _make_registry():
    return AppRegistry(workspace_name="Test Co", user_name="alice", user_role="owner")


def _jsonl(*events: dict) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode("utf-8")


def test_chat_parses_agent_end_message_and_tools():
    """Real pi 0.74 shape: tool_execution_start + agent_end with messages list."""
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl(
            {"type": "session", "id": "s1"},
            {"type": "agent_start"},
            {
                "type": "tool_execution_start",
                "toolCallId": "c1",
                "toolName": "bash",
                "args": {"command": "ls -la"},
            },
            {"type": "tool_execution_end", "toolCallId": "c1", "result": "two files"},
            {
                "type": "agent_end",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Done. Found 2 files."}],
                    },
                ],
            },
        )
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        fake_exec.captured_kwargs = kwargs
        return stub

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = asyncio.run(pi_rt.chat(reg, app, "list files", "sess-1"))

    assert result["text"] == "Done. Found 2 files."
    assert result["tools_used"] == ["bash"]
    assert any("ls -la" in o for o in result["tool_outputs"])
    assert fake_exec.captured_kwargs["cwd"] == "/tmp/ws-pi-test"


def test_chat_picks_provider_model_split():
    reg = _make_registry()
    app = _make_app(model="router/gpt-5.3-codex")
    stub = _StubProc(
        stdout=_jsonl(
            {
                "type": "agent_end",
                "messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                ],
            },
        )
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        return stub

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(pi_rt.chat(reg, app, "hi", "s"))

    args = fake_exec.captured_args
    # --provider router --model gpt-5.3-codex
    assert "--provider" in args
    assert args[args.index("--provider") + 1] == "router"
    assert "--model" in args
    assert args[args.index("--model") + 1] == "gpt-5.3-codex"


def test_chat_falls_back_to_default_provider_when_unprefixed():
    reg = _make_registry()
    app = _make_app(model="gpt-5.4")  # bare, no provider prefix
    stub = _StubProc(
        stdout=_jsonl(
            {
                "type": "agent_end",
                "messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                ],
            },
        )
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        return stub

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(pi_rt.chat(reg, app, "hi", "s"))
    args = fake_exec.captured_args
    assert args[args.index("--provider") + 1] == pi_rt.DEFAULT_PROVIDER
    assert args[args.index("--model") + 1] == "gpt-5.4"


def test_stream_yields_one_final_response():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl(
            {
                "type": "agent_end",
                "messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                ],
            },
        )
    )

    async def fake_exec(*args, **kwargs):
        return stub

    async def collect():
        return [ev async for ev in pi_rt.stream(reg, app, "hi", "s")]

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0]["type"] == "response"
    assert events[0]["text"] == "ok"


def test_pi_bin_env_overrides_binary():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl(
            {
                "type": "agent_end",
                "messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                ],
            },
        )
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        return stub

    with patch.dict(os.environ, {"PI_BIN": "/opt/custom/pi"}):
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            asyncio.run(pi_rt.chat(reg, app, "hi", "s"))
    assert fake_exec.captured_args[0] == "/opt/custom/pi"


def test_allowed_tools_passed_via_cli_flag():
    reg = _make_registry()
    app = _make_app(gates_config={"cli_allowed_tools": ["read", "bash"]})
    stub = _StubProc(
        stdout=_jsonl(
            {
                "type": "agent_end",
                "messages": [
                    {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                ],
            },
        )
    )

    async def fake_exec(*args, **kwargs):
        fake_exec.captured_args = args
        return stub

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(pi_rt.chat(reg, app, "hi", "s"))
    args = fake_exec.captured_args
    assert "--tools" in args
    assert args[args.index("--tools") + 1] == "read,bash"


def test_history_persisted_across_turns():
    reg = _make_registry()
    app = _make_app()

    def make_stub(reply: str):
        return _StubProc(
            stdout=_jsonl(
                {
                    "type": "agent_end",
                    "messages": [
                        {"role": "assistant", "content": [{"type": "text", "text": reply}]},
                    ],
                },
            )
        )

    stubs = [make_stub("first reply"), make_stub("second reply")]

    async def fake_exec(*args, **kwargs):
        idx = fake_exec.n
        fake_exec.n += 1
        return stubs[idx]

    fake_exec.n = 0

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(pi_rt.chat(reg, app, "first", "s"))
        asyncio.run(pi_rt.chat(reg, app, "second", "s"))

    history = reg._get_history("s")
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert history[1]["content"] == "first reply"
    assert history[3]["content"] == "second reply"


def test_timeout_returns_error_text_and_kills():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(hang=True)

    async def fake_exec(*args, **kwargs):
        return stub

    with patch.object(pi_rt, "DEFAULT_TIMEOUT_S", 0.05):
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = asyncio.run(pi_rt.chat(reg, app, "hi", "s"))
    assert "timed out" in result["text"]
    assert stub.killed is True


def test_build_gate_manager_returns_none():
    reg = _make_registry()
    app = _make_app()
    assert pi_rt.build_gate_manager(reg, app) is None


def test_get_or_create_agent_returns_sentinel():
    reg = _make_registry()
    app = _make_app()
    handle = pi_rt.get_or_create_agent(reg, app)
    assert isinstance(handle, dict)
    assert handle["runtime"] == "pi"
    assert pi_rt.get_or_create_agent(reg, app) is handle  # cached
