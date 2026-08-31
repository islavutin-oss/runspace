"""Tests for the `runspace` console-scripts dispatcher."""

from __future__ import annotations

import sys

import pytest

from runspace import runspace_cli


def test_help_lists_every_command(capsys):
    sys.argv = ["runspace", "--help"]
    with pytest.raises(SystemExit) as e:
        runspace_cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    for cmd in runspace_cli.COMMANDS:
        assert cmd in out


def test_no_args_exits_2_and_shows_help(capsys):
    sys.argv = ["runspace"]
    with pytest.raises(SystemExit) as e:
        runspace_cli.main()
    assert e.value.code == 2


def test_unknown_command_exits_2_with_message(capsys):
    sys.argv = ["runspace", "frobnicate"]
    with pytest.raises(SystemExit) as e:
        runspace_cli.main()
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "frobnicate" in err
    assert "unknown command" in err.lower()


def test_inspect_subcommand_dispatches(capsys, monkeypatch, tmp_path):
    """Verify `runspace inspect <ws.yml>` actually runs agents_inspect.main()."""
    import yaml

    soul = tmp_path / "agents" / "bot" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("# bot\n")
    ws = tmp_path / "workspace.yml"
    ws.write_text(
        yaml.safe_dump(
            {
                "name": "T",
                "providers": {},
                "apps": {"bot": {"type": "agentino", "soul": "agents/bot/SOUL.md"}},
            }
        )
    )

    sys.argv = ["runspace", "inspect", str(ws)]
    runspace_cli.main()  # should not raise / exit non-zero
    out = capsys.readouterr().out
    assert "bot" in out


def test_tools_diff_subcommand_dispatches(capsys, tmp_path):
    """Subcommand with its own subparsers (snapshot/diff) should still route."""
    import json

    import yaml

    ws = tmp_path / "workspace.yml"
    ws.write_text(
        yaml.safe_dump(
            {
                "name": "T",
                "providers": {},
                "apps": {},
            }
        )
    )
    snap = tmp_path / "snap.json"
    sys.argv = ["runspace", "tools-diff", "snapshot", str(ws), "--out", str(snap)]
    with pytest.raises(SystemExit) as e:
        runspace_cli.main()
    assert e.value.code == 0
    snapshot = json.loads(snap.read_text())
    assert snapshot["tenant"] == "T"


def test_argv_shifted_so_subcommand_sees_clean_args(monkeypatch):
    """The subcommand's argparse must see its own positional args at argv[1:].

    `runspace inspect /path/to/ws.yml` → the inspect script sees argv =
    ["runspace inspect", "/path/to/ws.yml"]. If we accidentally left
    "inspect" in argv, the inspect script's argparse would treat
    "inspect" as the workspace_yml positional.
    """
    captured: dict = {}

    def fake_main():
        captured["argv"] = list(sys.argv)

    # Inject a fake module
    import types

    fake_module = types.ModuleType("agents_inspect")
    fake_module.main = fake_main  # type: ignore[attr-defined]
    sys.modules["agents_inspect"] = fake_module
    try:
        sys.argv = ["runspace", "inspect", "/some/ws.yml"]
        runspace_cli.main()
    finally:
        del sys.modules["agents_inspect"]

    assert captured["argv"][0] == "runspace inspect"
    assert "/some/ws.yml" in captured["argv"]
    # And "inspect" must NOT be in the shifted argv
    assert "inspect" not in captured["argv"][1:]
