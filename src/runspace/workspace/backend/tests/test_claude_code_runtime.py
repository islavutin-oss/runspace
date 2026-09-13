"""Tests for runtimes/claude_code.py — mocked-subprocess adapter shape."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest

from runspace.workspace.backend.app_registry import AgentApp, AppRegistry
from runspace.workspace.backend.runtimes import claude_code as cc_rt
from runspace.workspace.backend.runtimes._failure import failure_text


@pytest.fixture(autouse=True)
def _restore_default_event_loop():
    """`asyncio.run` clears the thread-local loop; sibling tests that use
    the deprecated `asyncio.get_event_loop()` rely on one being set. Restore
    a fresh default loop after each test so cross-file ordering stays clean."""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


class _StubPipe:
    """A readable pipe fed from a list of lines; an optional delay between
    lines lets a test observe events arriving before the process is done."""

    def __init__(self, data: bytes, *, hang: bool = False, delay: float = 0.0):
        self._lines = data.splitlines(keepends=True)
        self._hang = hang
        self._delay = delay

    async def readline(self) -> bytes:
        if self._hang:
            await asyncio.sleep(3600)
        if not self._lines:
            return b""
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._lines.pop(0)

    async def read(self) -> bytes:
        return b"".join(self._lines)


class _StubStdin:
    def __init__(self):
        self.received = b""
        self.closed = False

    def write(self, data: bytes):
        self.received += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class _StubProc:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        *,
        hang: bool = False,
        delay: float = 0.0,
    ):
        self.stdout = _StubPipe(stdout, hang=hang, delay=delay)
        self.stderr = _StubPipe(stderr)
        self.stdin = _StubStdin()
        self.killed = False
        self.finished = False

    @property
    def received_stdin(self) -> bytes:
        return self.stdin.received

    def kill(self):
        self.killed = True

    async def wait(self):
        self.finished = True
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
    assert result["text"] == failure_text("timeout")
    assert stub.killed is True


def test_build_gate_manager_returns_none():
    reg = _make_registry()
    app = _make_app()
    assert cc_rt.build_gate_manager(reg, app) is None


def _tool_use(name: str, **inp) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]},
    }


def test_stream_announces_each_tool_call_before_the_reply():
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl(
            {"type": "system", "subtype": "init"},
            _tool_use("Read", path="x.txt"),
            _tool_use("Bash", command="ls"),
            {"type": "result", "result": "done"},
        )
    )

    async def fake_exec(*args, **kwargs):
        return stub

    async def collect():
        return [ev async for ev in cc_rt.stream(reg, app, "hi", "s")]

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = asyncio.run(collect())

    assert [e["type"] for e in events] == ["tool_call", "tool_call", "response"]
    assert [e["name"] for e in events[:2]] == ["Read", "Bash"]
    assert events[-1]["text"] == "done"
    assert events[-1]["tools_used"] == ["Read", "Bash"]


def test_tool_calls_are_relayed_while_the_process_is_still_running():
    """The point of streaming: the reader learns what is running before the
    turn ends, not after. With a delay between lines, the first tool_call
    must be observed before the stub has been waited on."""
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl(_tool_use("Read", path="x.txt"), {"type": "result", "result": "ok"}),
        delay=0.05,
    )

    async def fake_exec(*args, **kwargs):
        return stub

    seen_running: list[bool] = []

    async def collect():
        async for ev in cc_rt.stream(reg, app, "hi", "s"):
            if ev["type"] == "tool_call":
                seen_running.append(not stub.finished)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(collect())
    assert seen_running == [True]
    assert stub.finished is True


def test_dispatcher_calls_are_named_after_the_tool_not_the_shell():
    """An agent reaching its tools through `./tools call <name>` is calling
    <name>; that is what the reader sees and what tools_used records."""
    reg = _make_registry()
    app = _make_app()
    stub = _StubProc(
        stdout=_jsonl(
            _tool_use("Bash", command="./tools list"),
            _tool_use("Bash", command="./tools call lookup_order '{\"id\": 7}'"),
            _tool_use("Bash", command="cd /w && tools call summarize '{}' 2>&1 | head"),
            _tool_use("Read", path="notes.md"),
            {"type": "result", "result": "ok"},
        )
    )

    async def fake_exec(*args, **kwargs):
        return stub

    async def collect():
        return [ev async for ev in cc_rt.stream(reg, app, "hi", "s")]

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = asyncio.run(collect())
    names = [e["name"] for e in events if e["type"] == "tool_call"]
    assert names == ["Bash", "lookup_order", "summarize", "Read"]
    assert events[-1]["tools_used"] == names


def _dispatcher_transcript() -> bytes:
    return _jsonl(
        _tool_use("Bash", command="ls agents/"),
        _tool_use("Bash", command="./tools call lookup_order '{\"id\": 7}'"),
        _tool_use("Read", path="notes.md"),
        _tool_use("Bash", command="./tools call lookup_order '{\"id\": 8}'"),
        _tool_use("Bash", command="./tools call summarize '{}'"),
        {"type": "result", "result": "ok"},
    )


def _stream_names(app, stdout: bytes) -> tuple[list[str], list[str]]:
    reg = _make_registry()
    stub = _StubProc(stdout=stdout)

    async def fake_exec(*args, **kwargs):
        return stub

    async def collect():
        return [ev async for ev in cc_rt.stream(reg, app, "hi", "s")]

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        events = asyncio.run(collect())
    names = [e["name"] for e in events if e["type"] == "tool_call"]
    return names, events[-1]["tools_used"]


def test_with_a_dispatcher_only_the_agents_own_tools_are_reported(tmp_path):
    """Once the workspace reaches its tools through ./tools, the CLI's own
    Bash and Read are plumbing. A reader shown "Bash, Bash, lookup_order,
    Read, Bash" learns nothing from the Bashes; they are dropped from the
    events and from tools_used alike."""
    (tmp_path / "tools").write_text("#!/bin/sh\n")
    names, used = _stream_names(_make_app(workspace_path=str(tmp_path)), _dispatcher_transcript())
    assert names == ["lookup_order", "lookup_order", "summarize"]
    assert used == names


def test_without_a_dispatcher_the_cli_tools_are_all_there_is(tmp_path):
    names, used = _stream_names(_make_app(workspace_path=str(tmp_path)), _dispatcher_transcript())
    assert names == ["Bash", "lookup_order", "Read", "lookup_order", "summarize"]
    assert used == names


def test_chat_and_stream_share_one_history_write():
    reg = _make_registry()
    app = _make_app()

    def make_stub():
        return _StubProc(
            stdout=_jsonl(_tool_use("Read", path="a"), {"type": "result", "result": "r"})
        )

    async def fake_exec(*args, **kwargs):
        return make_stub()

    async def collect():
        return [ev async for ev in cc_rt.stream(reg, app, "one", "s")]

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(collect())
        asyncio.run(cc_rt.chat(reg, app, "two", "s"))
    roles = [(h["role"], h["content"]) for h in reg._get_history("s")]
    assert roles == [("user", "one"), ("assistant", "r"), ("user", "two"), ("assistant", "r")]


# ── against a real subprocess ─────────────────────────────────────────────
# The stubs above cannot prove the pipe handling: that stdin is fed without
# deadlocking on a large prompt, that events are read as they are written,
# that stderr is drained, and that a hung process is really killed.

_FAKE_CLAUDE = """\
#!/usr/bin/env python3
import json, os, sys, time
prompt = sys.stdin.read()
sys.stderr.write("noise " * 2000)
print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": "./tools call ping '{}'"}}]}}), flush=True)
time.sleep(float(os.environ.get("FAKE_PAUSE", "0.3")))
if os.environ.get("FAKE_HANG"):
    time.sleep(3600)
print(json.dumps({"type": "result", "result": f"prompt bytes={len(prompt.encode())}"}), flush=True)
"""


@pytest.fixture
def fake_claude(tmp_path):
    script = tmp_path / "claude"
    script.write_text(_FAKE_CLAUDE)
    script.chmod(0o755)
    with patch.dict(os.environ, {"CLAUDE_CODE_BIN": str(script)}):
        yield script


def test_real_subprocess_streams_the_tool_call_before_the_result(fake_claude, tmp_path):
    reg = _make_registry()
    app = _make_app(workspace_path=str(tmp_path))
    loop_times: list[tuple[str, float]] = []

    async def collect():
        loop = asyncio.get_running_loop()
        async for ev in cc_rt.stream(reg, app, "hi", "s"):
            loop_times.append((ev["type"], loop.time()))

    with patch.dict(os.environ, {"FAKE_PAUSE": "0.4"}):
        asyncio.run(collect())

    assert [t for t, _ in loop_times] == ["tool_call", "response"]
    # The tool_call was relayed during the pause, not after the process exited.
    assert loop_times[1][1] - loop_times[0][1] >= 0.3


def test_real_subprocess_takes_a_prompt_larger_than_a_pipe(fake_claude, tmp_path):
    reg = _make_registry()
    app = _make_app(workspace_path=str(tmp_path))
    app._soul_text = "x" * 300_000  # well past the 64 KiB pipe buffer

    with patch.dict(os.environ, {"FAKE_PAUSE": "0"}):
        result = asyncio.run(cc_rt.chat(reg, app, "hi", "s"))
    assert result["text"].startswith("prompt bytes=3000")
    assert result["tools_used"] == ["ping"]


def test_real_subprocess_is_killed_on_timeout(fake_claude, tmp_path):
    import subprocess

    reg = _make_registry()
    app = _make_app(workspace_path=str(tmp_path))

    async def collect():
        return [ev async for ev in cc_rt.stream(reg, app, "hi", "s")]

    with patch.dict(os.environ, {"FAKE_PAUSE": "0", "FAKE_HANG": "1"}):
        with patch.object(cc_rt, "DEFAULT_TIMEOUT_S", 0.5):
            events = asyncio.run(collect())

    assert [e["type"] for e in events] == ["tool_call", "response"]
    assert events[-1]["text"] == failure_text("timeout")
    left = subprocess.run(["pgrep", "-f", str(fake_claude)], capture_output=True, text=True)
    assert left.stdout.strip() == "", "the hung CLI process outlived its timeout"


# ── A refused call is not a call ────────────────────────────────────────────


def _refusal_transcript() -> str:
    """One allowed dispatcher call, one the gate refused, one that really failed."""
    return "\n".join(
        json.dumps(ev)
        for ev in [
            {"type": "system", "subtype": "init", "model": "claude-opus-5"},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Bash",
                            "input": {"command": "./tools call coverage"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "42 measured"}
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t2",
                            "name": "Bash",
                            "input": {"command": "./tools call publish_article"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t2",
                            "is_error": True,
                            "content": "This command requires approval",
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t3",
                            "name": "Bash",
                            "input": {"command": "./tools call best_value"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t3",
                            "is_error": True,
                            "content": "KeyError: 'price'",
                        }
                    ]
                },
            },
            {"type": "result", "result": "done"},
        ]
    )


def test_a_refused_call_is_not_reported_as_used():
    """The gate refuses a call before it runs, and the CLI reports that as a
    tool_result error. Counting it left the reply saying it used a tool that
    never executed — on one deployment, twelve of them."""
    state = cc_rt._StreamState(own_tools_only=True)
    for ev in cc_rt._events_from_lines(_refusal_transcript().splitlines()):
        state.feed(ev)
    assert state.tools_used == ["coverage", "best_value"]
    assert state.refused == ["publish_article"]


def test_a_tool_that_ran_and_failed_is_still_a_tool_that_ran():
    state = cc_rt._StreamState(own_tools_only=True)
    for ev in cc_rt._events_from_lines(_refusal_transcript().splitlines()):
        state.feed(ev)
    assert "best_value" in state.tools_used, "a failing tool still ran"
    assert any("KeyError" in o for o in state.tool_outputs)


def test_the_refusal_text_is_not_offered_as_tool_output():
    """Anything reading tool output as evidence — a widget validator, a guard
    checking a number came from somewhere — must not be handed the CLI's
    refusal as if it were a result."""
    state = cc_rt._StreamState(own_tools_only=True)
    for ev in cc_rt._events_from_lines(_refusal_transcript().splitlines()):
        state.feed(ev)
    assert not any("requires approval" in o for o in state.tool_outputs)
