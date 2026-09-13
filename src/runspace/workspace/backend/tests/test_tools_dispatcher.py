"""An agent moved onto a CLI harness must keep its tools.

A CLI runtime is not handed a tools array: it drives its own loop and reaches
the outside world through Bash. An agent migrated from an in-process runtime
therefore arrives with no tools at all unless something puts them back within
reach — and the failure is quiet. The agent is told in its instructions that
tools exist, finds it cannot call any, and answers from memory. A wrong path
becomes a fabricated answer, which is worse than an error.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from runspace.workspace.backend.app_registry import AgentApp
from runspace.workspace.backend.runtimes import tools_cli
from runspace.workspace.backend.runtimes.claude_code import (
    _ensure_dispatcher,
)


def _app(**kw) -> AgentApp:
    kw.setdefault("id", "desk")
    kw.setdefault("name", "Desk")
    return AgentApp(**kw)


class _Registry:
    """Just the attribute the dispatcher reads."""

    def __init__(self, *apps):
        self.apps = {a.id: a for a in apps}


def test_a_workspace_with_tools_gets_a_dispatcher(tmp_path):
    _ensure_dispatcher(str(tmp_path), _app(tools_dir="agents/desk/tools"))
    shim = tmp_path / "tools"
    assert shim.is_file()
    assert os.access(shim, os.X_OK), "a dispatcher the shell cannot execute is no dispatcher"


def test_the_dispatcher_names_the_interpreter_running_the_server(tmp_path):
    """Not `python3` off the PATH.

    The subprocess has to import both this package and the agent framework.
    The interpreter already running the server can do that by construction;
    whatever `python3` resolves to inside a container generally cannot, and
    the resulting failure looks like a missing tool rather than a missing venv.
    """
    _ensure_dispatcher(str(tmp_path), _app(tools_dir="tools"))
    assert sys.executable in (tmp_path / "tools").read_text()


def test_shared_tool_trees_are_included(tmp_path):
    _ensure_dispatcher(
        str(tmp_path),
        _app(tools_dir="agents/desk/tools", shared_tools_dirs=["shared/a", "shared/b"]),
    )
    line = [
        ln for ln in (tmp_path / "tools").read_text().splitlines() if "RUNSPACE_TOOL_DIRS" in ln
    ][0]
    for expected in ("agents/desk/tools", "shared/a", "shared/b"):
        assert expected in line


def test_an_agent_with_no_tools_gets_no_dispatcher(tmp_path):
    """An empty dispatcher would advertise a capability that is not there."""
    _ensure_dispatcher(str(tmp_path), _app())
    assert not (tmp_path / "tools").exists()


def test_a_hand_written_dispatcher_is_never_clobbered(tmp_path):
    """A workspace that wrote its own had a reason."""
    shim = tmp_path / "tools"
    shim.write_text("#!/bin/sh\necho mine\n")
    shim.chmod(0o755)
    _ensure_dispatcher(str(tmp_path), _app(tools_dir="tools"))
    assert shim.read_text() == "#!/bin/sh\necho mine\n"


def test_our_own_dispatcher_is_refreshed_when_the_config_moves(tmp_path):
    """Otherwise a renamed tool tree leaves a shim pointing at nothing."""
    _ensure_dispatcher(str(tmp_path), _app(tools_dir="old/place"))
    _ensure_dispatcher(str(tmp_path), _app(tools_dir="new/place"))
    body = (tmp_path / "tools").read_text()
    assert "new/place" in body
    assert "old/place" not in body


def test_an_unwritable_workspace_does_not_take_the_turn_down(tmp_path):
    """Losing the tools is bad; losing the answer as well is worse."""
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        _ensure_dispatcher(str(ro), _app(tools_dir="tools"))
    finally:
        ro.chmod(0o755)


# ── the dispatcher itself ───────────────────────────────────────────────────


def test_nothing_is_hardcoded_into_the_dispatcher():
    """It named one deployment's directories once. Then it worked there and
    nowhere else."""
    source = Path(tools_cli.__file__).read_text(encoding="utf-8")
    assert "DEFAULT_TOOL_DIRS" not in source
    for leaked in ("agents/advisor", "agents/editor", "agents/providers"):
        assert leaked not in source


def test_no_tool_directories_means_no_tools(monkeypatch):
    monkeypatch.delenv("RUNSPACE_TOOL_DIRS", raising=False)
    assert tools_cli.load_tools() == {}


def test_a_missing_directory_is_reported_not_silently_skipped(monkeypatch, capsys, tmp_path):
    """Silence here is how an agent ends up with no tools and no explanation."""
    monkeypatch.setenv("RUNSPACE_TOOLS_ROOT", str(tmp_path))
    monkeypatch.setenv("RUNSPACE_TOOL_DIRS", "not/here")
    tools_cli.load_tools()
    assert "not/here" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["list", "schema", "call"])
def test_every_command_is_reachable(command, monkeypatch, tmp_path):
    monkeypatch.setenv("RUNSPACE_TOOLS_ROOT", str(tmp_path))
    monkeypatch.setenv("RUNSPACE_TOOL_DIRS", "")
    # No tools loaded, so schema/call report the miss and list prints nothing.
    argv = [command] if command == "list" else [command, "nope"]
    assert tools_cli.main(argv) in (0, 1)


def test_help_does_not_need_the_framework(monkeypatch):
    """`--help` must work before anything is installed or configured."""
    assert tools_cli.main(["--help"]) == 0


# ── agents that share a workspace share the dispatcher ──────────────────────


def test_every_agent_in_a_workspace_is_in_the_dispatcher(tmp_path):
    """Agents commonly share a workspace directory, and the dispatcher is one
    file in it. Built from a single agent, whichever spoke last would leave the
    others pointing at trees that are not theirs."""
    ws = str(tmp_path)
    finance = _app(id="finance", workspace_path=ws, tools_dir="agents/finance/tools")
    booking = _app(id="booking", workspace_path=ws, tools_dir="agents/booking/tools")

    _ensure_dispatcher(ws, finance, _Registry(finance, booking))

    body = (tmp_path / "tools").read_text()
    assert "agents/finance/tools" in body
    assert "agents/booking/tools" in body


def test_an_agent_in_a_different_workspace_is_left_out(tmp_path):
    """Sharing the file is scoped to sharing the directory."""
    ws, other = str(tmp_path / "a"), str(tmp_path / "b")
    (tmp_path / "a").mkdir()
    here = _app(id="here", workspace_path=ws, tools_dir="mine/tools")
    away = _app(id="away", workspace_path=other, tools_dir="theirs/tools")

    _ensure_dispatcher(ws, here, _Registry(here, away))

    body = (tmp_path / "a" / "tools").read_text()
    assert "mine/tools" in body
    assert "theirs/tools" not in body


def test_a_tools_directory_is_not_mistaken_for_a_stale_shim(tmp_path, caplog):
    """Some workspaces keep their tools in a directory called `tools`. Writing
    a file over it would be a data loss, and failing silently would leave an
    agent toolless with no explanation."""
    import logging

    (tmp_path / "tools").mkdir()
    with caplog.at_level(logging.WARNING):
        _ensure_dispatcher(str(tmp_path), _app(tools_dir="x"))
    assert (tmp_path / "tools").is_dir()
    assert "is a directory" in caplog.text


def test_a_declared_workspace_is_created_if_absent(tmp_path):
    """Two desks each owning a `generate_report` must not share a dispatcher —
    the loader keeps the first and the other desk silently runs the wrong tool.
    Separate workspaces are the fix, and they exist only because the config
    asked for them."""
    ws = tmp_path / "desks" / "finance"
    _ensure_dispatcher(str(ws), _app(tools_dir="agents/finance/tools"))
    assert (ws / "tools").is_file()


# ── a desk can declare its own workspace directory ──────────────────────────


def test_apps_share_the_workspace_directory_by_default():
    from runspace.workspace.backend.gateway import _app_workspace

    assert _app_workspace(Path("/srv/tenant"), {}) == "/srv/tenant"


def test_an_app_can_declare_its_own_workspace():
    """Two desks each owning a `generate_report` cannot share a dispatcher."""
    from runspace.workspace.backend.gateway import _app_workspace

    assert (
        _app_workspace(Path("/srv/tenant"), {"workspace_path": "/srv/desks/finance"})
        == "/srv/desks/finance"
    )


def test_a_relative_workspace_resolves_against_the_config_not_the_cwd():
    """The process's working directory is not something a config author can
    see, and it differs between the server, a routine and a test."""
    from runspace.workspace.backend.gateway import _app_workspace

    assert (
        _app_workspace(Path("/srv/tenant"), {"workspace_path": "desks/finance"})
        == "/srv/tenant/desks/finance"
    )


def test_the_shim_hands_down_the_servers_import_path(tmp_path):
    """Tools import the application they belong to. The server resolves those
    by construction; a bare subprocess does not, and the loader reports each
    miss and carries on — so the agent holds some of its tools and never learns
    which ones are missing."""
    _ensure_dispatcher(str(tmp_path), _app(tools_dir="tools"))
    body = (tmp_path / "tools").read_text()
    assert "PYTHONPATH=" in body
    assert sys.path[0] in body or any(p and p in body for p in sys.path)


def test_tool_directories_are_normalised(tmp_path):
    """Config paths are joined, not resolved. Two spellings of one directory
    should not read as two directories."""
    _ensure_dispatcher(str(tmp_path), _app(tools_dir="/srv/t/../../agents/x/tools"))
    body = (tmp_path / "tools").read_text()
    assert "/agents/x/tools" in body
    assert ".." not in body.split("RUNSPACE_TOOL_DIRS=")[1].split("\n")[0]


# ── the agent has to be told the dispatcher exists ──────────────────────────


def test_no_harness_note_without_a_dispatcher(tmp_path):
    """Nothing to describe, and describing it anyway invents a capability."""
    from runspace.workspace.backend.runtimes.claude_code import _harness_note

    assert _harness_note(str(tmp_path), ["Bash(./tools call x:*)"]) == ""


def test_the_note_says_the_tools_are_a_shell_command(tmp_path):
    """A SOUL written for a runtime handed a tools array says "use your
    tools" — true but unactionable on a CLI harness, where the agent hunts,
    finds nothing, and reports a tooling problem to the user."""
    from runspace.workspace.backend.runtimes.claude_code import _harness_note

    _ensure_dispatcher(str(tmp_path), _app(tools_dir="tools"))
    note = _harness_note(str(tmp_path), None)
    assert "./tools call" in note
    assert "./tools list" in note


def test_the_note_names_only_what_the_allowlist_permits(tmp_path):
    """Naming a tool the harness will refuse sends the agent at a wall."""
    from runspace.workspace.backend.runtimes.claude_code import _harness_note

    _ensure_dispatcher(str(tmp_path), _app(tools_dir="tools"))
    note = _harness_note(
        str(tmp_path),
        [
            "Bash(./tools list:*)",
            "Bash(./tools call get_revenue_summary:*)",
            "Bash(./tools call get_cash_flow:*)",
        ],
    )
    assert "get_revenue_summary" in note
    assert "get_cash_flow" in note


def test_the_note_survives_an_empty_allowlist(tmp_path):
    """No per-app list means the CLI's own default, not "no tools"."""
    from runspace.workspace.backend.runtimes.claude_code import _harness_note

    _ensure_dispatcher(str(tmp_path), _app(tools_dir="tools"))
    assert _harness_note(str(tmp_path), []).strip()


# ── ambient context has to cross the process boundary ───────────────────────


class _Reg(_Registry):
    def __init__(self, *apps, tenant_id="acme"):
        super().__init__(*apps)
        self.tenant_id = tenant_id


def test_the_shim_carries_the_tenant(tmp_path):
    """In-process a tool reads the tenant from a ContextVar the server sets.
    A subprocess starts with it empty, and the same tool either refuses or —
    where it does not check — answers for the wrong tenant."""
    app = _app(tools_dir="tools")
    _ensure_dispatcher(str(tmp_path), app, _Reg(app, tenant_id="acme"))
    assert '"tenant_id": "acme"' in (tmp_path / "tools").read_text()


def test_seeding_survives_a_missing_variable(monkeypatch):
    from runspace.workspace.backend.runtimes import tools_cli

    monkeypatch.delenv("RUNSPACE_TOOL_CONTEXT", raising=False)
    assert tools_cli.seed_context() == {}


def test_seeding_reports_malformed_json_rather_than_dying(monkeypatch, capsys):
    """A tool run with no context is recoverable; a dispatcher that will not
    start is not."""
    from runspace.workspace.backend.runtimes import tools_cli

    monkeypatch.setenv("RUNSPACE_TOOL_CONTEXT", "{not json")
    assert tools_cli.seed_context() == {}
    assert "not valid JSON" in capsys.readouterr().err


def test_seeded_context_reaches_the_framework(monkeypatch):
    from runspace.workspace.backend.runtimes import tools_cli

    monkeypatch.setenv("RUNSPACE_TOOL_CONTEXT", '{"tenant_id": "acme"}')
    seeded = tools_cli.seed_context()
    if seeded:  # only when the framework is installed
        from agentino.core.context import get_context

        assert get_context("tenant_id") == "acme"


# ── a tool-rendered chart must survive the process boundary ─────────────────


def test_a_registered_block_is_spliced_before_printing():
    """A tool returning a chart registers the real block and emits
    `{"$mcpui": 0}` for the caller to splice. In-process that works — the block
    is in a shared ContextVar. Across a process boundary it cannot, and every
    tool-rendered chart reached the model as the placeholder."""
    from runspace.workspace.backend._mcp_ui import begin_turn, register_block
    from runspace.workspace.backend.runtimes.tools_cli import _restore_blocks

    begin_turn()
    real = '```chart\n{"type": "line", "data": [{"x": 1, "y": 2}]}\n```'
    placeholder = register_block(real)
    assert '"$mcpui"' in placeholder

    out = _restore_blocks(f"Here is the sweep.\n{placeholder}\n")
    assert '"$mcpui"' not in out, "the placeholder reached the caller"
    assert '"type": "line"' in out


def test_text_without_a_block_is_untouched():
    from runspace.workspace.backend._mcp_ui import begin_turn
    from runspace.workspace.backend.runtimes.tools_cli import _restore_blocks

    begin_turn()
    assert _restore_blocks("just a sentence") == "just a sentence"


def test_restoring_never_loses_the_answer(monkeypatch):
    """Formatting is worth less than the reply it decorates."""
    from runspace.workspace.backend.runtimes import tools_cli

    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr("runspace.workspace.backend._mcp_ui.restore_mcp_ui_blocks", boom)
    assert tools_cli._restore_blocks("the answer") == "the answer"
