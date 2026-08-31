"""Tests for runtimes/openclaw.py — the adapter shape, with a stubbed CLI.

The adapter was the only runtime shipping untested, which matters because its
whole job is parsing another tool's output: the shapes below are what it has
to survive when OpenClaw changes them between point releases.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from runspace.workspace.backend.app_registry import AgentApp, AppRegistry
from runspace.workspace.backend.runtimes import openclaw as rt


@pytest.fixture(autouse=True)
def _restore_default_event_loop():
    """asyncio.run clears the thread-local loop; restore one so sibling
    tests that still rely on a default loop are not affected by ordering."""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


class _StubProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout, self._stderr = stdout, stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self, stdin: bytes | None = None):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _app(**kw) -> AgentApp:
    return AgentApp(id="oc-bot", name="Claw", type="openclaw", **kw)


# ── configuration resolution ──────────────────────────────────────────────


def test_profile_prefers_the_explicit_setting():
    app = _app(gates_config={"openclaw_profile": "acme-prod"})
    assert rt._resolve_profile(AppRegistry(tenant_id="acme"), app) == "acme-prod"


def test_profile_falls_back_to_the_tenant_then_to_default():
    assert rt._resolve_profile(AppRegistry(tenant_id="acme"), _app()) == "acme"
    assert rt._resolve_profile(AppRegistry(), _app()) == "default"


def test_agent_id_and_model_come_from_the_app():
    app = _app(gates_config={"openclaw_agent": "reviewer"}, model="some-model")
    assert rt._resolve_agent_id(app) == "reviewer"
    assert rt._resolve_model(app) == "some-model"


def test_model_falls_back_to_the_adapter_default():
    assert rt._resolve_model(_app()) == rt.DEFAULT_MODEL


# ── output parsing: the part that breaks when OpenClaw changes ────────────


def test_parses_a_single_json_object():
    text, tools, outputs = rt._parse_openclaw_json(
        json.dumps({"result": "All done.", "toolCalls": [{"name": "search", "result": "3 hits"}]})
    )
    assert text == "All done."
    assert tools == ["search"]
    assert outputs == ["3 hits"]


def test_parses_a_json_lines_stream():
    lines = "\n".join(
        json.dumps(o)
        for o in ({"type": "start"}, {"result": "Finished.", "toolCalls": [{"name": "grep"}]})
    )
    text, tools, _ = rt._parse_openclaw_json(lines)
    assert text == "Finished."
    assert tools == ["grep"]


def test_reads_the_assistant_message_shape_too():
    text, _, _ = rt._parse_openclaw_json(
        json.dumps({"assistantMessage": {"content": "From the other shape."}})
    )
    assert text == "From the other shape."


@pytest.mark.parametrize("blob", ["", "   ", "not json at all", "{oops"])
def test_unparseable_output_degrades_instead_of_raising(blob):
    """A point release that changes the format must not take the app down."""
    text, tools, outputs = rt._parse_openclaw_json(blob)
    assert (text, tools, outputs) == ("", [], [])


# ── the turn ──────────────────────────────────────────────────────────────


def test_chat_returns_the_parsed_reply():
    payload = json.dumps({"result": "42", "toolCalls": [{"name": "calc", "result": "42"}]})

    async def fake_exec(*args, **kwargs):
        return _StubProc(stdout=payload.encode())

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        out = asyncio.run(rt.chat(AppRegistry(tenant_id="acme"), _app(), "what is 6*7?", "s1"))
    assert out["text"] == "42"
    assert out["tools_used"] == ["calc"]


def test_a_failing_cli_surfaces_its_stderr_rather_than_an_empty_reply():
    async def fake_exec(*args, **kwargs):
        return _StubProc(stdout=b"", stderr=b"openclaw: profile not found", returncode=1)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        out = asyncio.run(rt.chat(AppRegistry(tenant_id="acme"), _app(), "hello", "s1"))
    assert "profile not found" in out["text"]


def test_the_registry_dispatches_openclaw_apps_to_this_adapter():
    """The claim that runspace is runtime-agnostic rests on this branch."""
    import inspect

    from runspace.workspace.backend import app_registry

    src = inspect.getsource(app_registry)
    assert 'app.type == "openclaw"' in src
    for other in ("agentino", "codex", "claude_code", "pi"):
        assert f'app.type == "{other}"' in src, f"{other} lost its dispatch branch"
