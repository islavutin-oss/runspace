"""Tests for tools_usage.py — the JSONL telemetry layer + AppRegistry hook."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from runspace.workspace.backend import tools_usage
from runspace.workspace.backend.app_registry import AgentApp, AppRegistry


@pytest.fixture(autouse=True)
def _restore_default_event_loop():
    """`asyncio.run` clears the thread-local loop; sibling tests rely on one."""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------------
# tools_usage module — pure-function layer
# ---------------------------------------------------------------------------


def test_record_tool_calls_appends_one_line_per_tool(tmp_path: Path):
    log_path = tmp_path / "usage.jsonl"
    n = tools_usage.record_tool_calls(
        tenant="acme",
        agent="bot",
        session_key="s1",
        tools=["list_invoices", "due_today"],
        turn_elapsed_ms=2500,
        path=log_path,
    )
    assert n == 2
    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line]
    assert len(rows) == 2
    assert {r["tool"] for r in rows} == {"list_invoices", "due_today"}
    for r in rows:
        assert r["tenant"] == "acme"
        assert r["agent"] == "bot"
        assert r["session_key"] == "s1"
        assert r["turn_elapsed_ms"] == 2500
        assert r["ts"].endswith("Z")


def test_record_tool_calls_empty_tools_is_noop(tmp_path: Path):
    log_path = tmp_path / "usage.jsonl"
    n = tools_usage.record_tool_calls(
        tenant="x",
        agent="y",
        session_key=None,
        tools=[],
        path=log_path,
    )
    assert n == 0
    assert not log_path.exists()


def test_record_tool_calls_swallows_io_errors(tmp_path: Path, caplog):
    """Failures must NOT propagate — telemetry can never break the agent loop."""
    tmp_path / "no" / "such" / "dir" / "log.jsonl"
    # mkdir will succeed (parents=True), so we need a different failure mode
    # Create the parent as a *file* to make mkdir succeed but write fail
    blocker = tmp_path / "blocked"
    blocker.write_text("file not dir")  # parent of "blocked/log.jsonl" can't be dir
    log_path = blocker / "log.jsonl"
    import logging

    caplog.set_level(logging.WARNING)
    n = tools_usage.record_tool_calls(
        tenant="x",
        agent="y",
        session_key=None,
        tools=["t1"],
        path=log_path,
    )
    assert n == 0  # didn't write
    # And no exception escaped


def test_query_filters_by_tenant_and_agent(tmp_path: Path):
    log_path = tmp_path / "usage.jsonl"
    tools_usage.record_tool_calls(
        tenant="acme", agent="bot1", session_key="s", tools=["a", "b"], path=log_path
    )
    tools_usage.record_tool_calls(
        tenant="acme", agent="bot2", session_key="s", tools=["c"], path=log_path
    )
    tools_usage.record_tool_calls(
        tenant="other", agent="bot1", session_key="s", tools=["d"], path=log_path
    )

    all_rows = tools_usage.query(path=log_path)
    assert len(all_rows) == 4

    acme = tools_usage.query(tenant="acme", path=log_path)
    assert len(acme) == 3

    bot1 = tools_usage.query(agent="bot1", path=log_path)
    assert len(bot1) == 3

    acme_bot1 = tools_usage.query(tenant="acme", agent="bot1", path=log_path)
    assert len(acme_bot1) == 2
    assert {r["tool"] for r in acme_bot1} == {"a", "b"}


def test_query_filters_by_tool_and_time_range(tmp_path: Path):
    log_path = tmp_path / "usage.jsonl"
    # Manually craft rows with controlled timestamps for the time-filter assertion
    log_path.write_text(
        '{"ts": "2026-04-01T00:00:00Z", "tenant": "x", "agent": "y", "tool": "old_tool", "session_key": null}\n'
        '{"ts": "2026-05-01T12:00:00Z", "tenant": "x", "agent": "y", "tool": "mid_tool", "session_key": null}\n'
        '{"ts": "2026-05-09T00:00:00Z", "tenant": "x", "agent": "y", "tool": "new_tool", "session_key": null}\n'
    )

    only_new = tools_usage.query(tool="new_tool", path=log_path)
    assert [r["tool"] for r in only_new] == ["new_tool"]

    since_may = tools_usage.query(since="2026-05-01T00:00:00Z", path=log_path)
    assert {r["tool"] for r in since_may} == {"mid_tool", "new_tool"}

    until_may1 = tools_usage.query(until="2026-05-01T12:00:00Z", path=log_path)
    assert {r["tool"] for r in until_may1} == {"old_tool", "mid_tool"}


def test_query_returns_empty_when_log_missing(tmp_path: Path):
    rows = tools_usage.query(path=tmp_path / "nonexistent.jsonl")
    assert rows == []


def test_query_skips_malformed_lines(tmp_path: Path):
    log_path = tmp_path / "usage.jsonl"
    log_path.write_text(
        "not json at all\n"
        '{"ts": "2026-05-09T00:00:00Z", "tenant": "x", "agent": "y", "tool": "good", "session_key": null}\n'
        "also broken\n"
    )
    rows = tools_usage.query(path=log_path)
    assert len(rows) == 1
    assert rows[0]["tool"] == "good"


def test_aggregate_by_groups_correctly(tmp_path: Path):
    rows = [
        {"tenant": "acme", "agent": "bot", "tool": "list", "ts": "x"},
        {"tenant": "acme", "agent": "bot", "tool": "list", "ts": "x"},
        {"tenant": "acme", "agent": "bot", "tool": "due", "ts": "x"},
        {"tenant": "acme", "agent": "other", "tool": "list", "ts": "x"},
    ]
    by_tool = tools_usage.aggregate_by(rows, "tool")
    assert by_tool == {("list",): 3, ("due",): 1}

    by_agent_tool = tools_usage.aggregate_by(rows, "agent", "tool")
    assert by_agent_tool == {
        ("bot", "list"): 2,
        ("bot", "due"): 1,
        ("other", "list"): 1,
    }


# ---------------------------------------------------------------------------
# AppRegistry hook integration
# ---------------------------------------------------------------------------


def _stub_runtime_chat(tools_used: list[str]):
    async def _chat(*_args, **_kwargs):
        return {"text": "ok", "tools_used": tools_used, "tool_outputs": []}

    return _chat


def test_registry_does_not_record_when_telemetry_disabled(tmp_path: Path):
    """Default: record_tool_usage=False — no JSONL writes."""
    log_path = tmp_path / "usage.jsonl"
    reg = AppRegistry(workspace_name="Test", tenant_id="acme")
    reg.apps["bot"] = AgentApp(id="bot", name="Bot", type="agentino")

    stub_chat = _stub_runtime_chat(["list_invoices"])
    with patch.dict(os.environ, {"RUNSPACE_TOOL_USAGE_PATH": str(log_path)}):
        with patch("runspace.workspace.backend.runtimes.agentino.chat", new=stub_chat):
            asyncio.run(reg.chat("bot", "hi", "s"))

    assert not log_path.exists()  # opt-in only


def test_registry_records_tool_usage_when_enabled(tmp_path: Path):
    log_path = tmp_path / "usage.jsonl"
    # Need to override the module-level constant since record_tool_usage uses the env-driven default
    with patch.object(tools_usage, "DEFAULT_USAGE_PATH", log_path):
        reg = AppRegistry(workspace_name="Test", tenant_id="acme")
        reg.record_tool_usage = True
        reg.apps["bot"] = AgentApp(id="bot", name="Bot", type="agentino")

        stub_chat = _stub_runtime_chat(["list_invoices", "due_today"])
        with patch("runspace.workspace.backend.runtimes.agentino.chat", new=stub_chat):
            asyncio.run(reg.chat("bot", "hi", "session-1"))

    rows = [json.loads(ln) for ln in log_path.read_text().splitlines() if ln]
    assert len(rows) == 2
    assert {r["tool"] for r in rows} == {"list_invoices", "due_today"}
    for r in rows:
        assert r["tenant"] == "acme"
        assert r["agent"] == "bot"
        assert r["session_key"] == "session-1"
        assert r["turn_elapsed_ms"] >= 0


def test_registry_records_for_cli_runtimes_too(tmp_path: Path):
    """Telemetry hooks every runtime path, not just agentino."""
    log_path = tmp_path / "usage.jsonl"
    with patch.object(tools_usage, "DEFAULT_USAGE_PATH", log_path):
        reg = AppRegistry(workspace_name="Test", tenant_id="acme")
        reg.record_tool_usage = True
        reg.apps["cx"] = AgentApp(id="cx", name="Cx", type="codex")
        reg.apps["pi"] = AgentApp(id="pi", name="Pi", type="pi")

        stub_codex = _stub_runtime_chat(["bash"])
        stub_pi = _stub_runtime_chat(["bash", "bash"])
        with (
            patch("runspace.workspace.backend.runtimes.codex.chat", new=stub_codex),
            patch("runspace.workspace.backend.runtimes.pi.chat", new=stub_pi),
        ):
            asyncio.run(reg.chat("cx", "hi", "s"))
            asyncio.run(reg.chat("pi", "hi", "s"))

    rows = [json.loads(ln) for ln in log_path.read_text().splitlines() if ln]
    by_agent = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append(r["tool"])
    assert by_agent["cx"] == ["bash"]
    assert by_agent["pi"] == ["bash", "bash"]


def test_registry_recording_failure_does_not_break_chat(tmp_path: Path):
    """If the JSONL write fails (disk full, perms, …) chat() must still return."""
    reg = AppRegistry(workspace_name="Test", tenant_id="acme")
    reg.record_tool_usage = True
    reg.apps["bot"] = AgentApp(id="bot", name="Bot", type="agentino")

    stub_chat = _stub_runtime_chat(["list_invoices"])

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full simulation")

    with (
        patch("runspace.workspace.backend.runtimes.agentino.chat", new=stub_chat),
        patch("runspace.workspace.backend.tools_usage.record_tool_calls", side_effect=_boom),
    ):
        result = asyncio.run(reg.chat("bot", "hi", "s"))
    assert result["text"] == "ok"  # chat returned cleanly despite recording crash
