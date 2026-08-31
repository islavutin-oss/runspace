"""Tests for tools_diff.py — snapshot + diff."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import tools_diff  # noqa: E402  (after sys.path setup)


def _catalog(apps: list[dict]) -> dict:
    return {
        "tenant": "Test",
        "tenant_id": "test",
        "config": "ws.yml",
        "providers": ["router"],
        "apps": apps,
    }


def _app(app_id: str, tools: list[dict]) -> dict:
    return {
        "id": app_id,
        "name": app_id.capitalize(),
        "role": "",
        "type": "agentino",
        "model": "gpt-5.3-codex",
        "soul": "soul.md",
        "tools": tools,
    }


def _tool(name: str, params: list[dict] | None = None) -> dict:
    return {"name": name, "description": "doc", "params": params or []}


def _param(name: str, type_: str = "string", required: bool = False) -> dict:
    return {"name": name, "type": type_, "required": required, "description": ""}


# ---------------------------------------------------------------------------
# diff_catalogs
# ---------------------------------------------------------------------------


def test_no_changes_yields_empty_diff():
    cat = _catalog([_app("bot", [_tool("foo")])])
    diff = tools_diff.diff_catalogs(cat, cat)
    assert diff["apps_added"] == []
    assert diff["apps_removed"] == []
    assert diff["apps_changed"] == []
    assert all(v == 0 for v in diff["summary"].values())


def test_app_added_detected():
    before = _catalog([_app("a", [])])
    after = _catalog([_app("a", []), _app("b", [_tool("x")])])
    diff = tools_diff.diff_catalogs(before, after)
    assert [x["id"] for x in diff["apps_added"]] == ["b"]
    assert diff["apps_added"][0]["n_tools"] == 1
    assert diff["summary"]["added_apps"] == 1


def test_app_removed_detected():
    before = _catalog([_app("a", []), _app("b", [])])
    after = _catalog([_app("a", [])])
    diff = tools_diff.diff_catalogs(before, after)
    assert [x["id"] for x in diff["apps_removed"]] == ["b"]
    assert diff["summary"]["removed_apps"] == 1


def test_tool_added_within_app():
    before = _catalog([_app("bot", [_tool("foo")])])
    after = _catalog([_app("bot", [_tool("foo"), _tool("bar")])])
    diff = tools_diff.diff_catalogs(before, after)
    assert diff["apps_added"] == []
    assert len(diff["apps_changed"]) == 1
    changed = diff["apps_changed"][0]
    assert [t["name"] for t in changed["tools_added"]] == ["bar"]
    assert changed["tools_removed"] == []
    assert diff["summary"]["added_tools"] == 1


def test_tool_removed_within_app():
    before = _catalog([_app("bot", [_tool("foo"), _tool("bar")])])
    after = _catalog([_app("bot", [_tool("foo")])])
    diff = tools_diff.diff_catalogs(before, after)
    assert [t["name"] for t in diff["apps_changed"][0]["tools_removed"]] == ["bar"]


def test_tool_signature_change_detected():
    """Param added → signature changed → flagged."""
    before = _catalog([_app("bot", [_tool("foo", [_param("x", "string", True)])])])
    after = _catalog(
        [
            _app(
                "bot",
                [
                    _tool(
                        "foo",
                        [
                            _param("x", "string", True),
                            _param("y", "integer", False),
                        ],
                    )
                ],
            )
        ]
    )
    diff = tools_diff.diff_catalogs(before, after)
    assert len(diff["apps_changed"]) == 1
    changes = diff["apps_changed"][0]["tools_changed"]
    assert len(changes) == 1
    assert changes[0]["name"] == "foo"
    assert "y:integer?" in changes[0]["after_signature"]
    assert "y:integer?" not in changes[0]["before_signature"]


def test_param_required_to_optional_is_a_change():
    """`required → optional` for a param flips the sig (marked with `?`)."""
    before = _catalog([_app("bot", [_tool("foo", [_param("x", "string", True)])])])
    after = _catalog([_app("bot", [_tool("foo", [_param("x", "string", False)])])])
    diff = tools_diff.diff_catalogs(before, after)
    changes = diff["apps_changed"][0]["tools_changed"]
    assert len(changes) == 1
    assert changes[0]["before_signature"] == "(x:string)"
    assert changes[0]["after_signature"] == "(x:string?)"


def test_description_only_change_is_NOT_flagged_as_signature_change():
    """Reword a tool's description, keep params the same → not a sig change."""
    a_tool = _tool("foo", [_param("x")])
    a_tool["description"] = "Original docstring."
    b_tool = _tool("foo", [_param("x")])
    b_tool["description"] = "Reworded docstring."
    before = _catalog([_app("bot", [a_tool])])
    after = _catalog([_app("bot", [b_tool])])
    diff = tools_diff.diff_catalogs(before, after)
    assert diff["apps_changed"] == []  # signature alone is what matters here


def test_summary_aggregates_correctly():
    before = _catalog(
        [
            _app("a", [_tool("t1"), _tool("t2")]),
            _app("removed", []),
        ]
    )
    after = _catalog(
        [
            _app("a", [_tool("t1"), _tool("t2_renamed")]),  # 1 added, 1 removed
            _app("new_app", [_tool("nt")]),  # +app +tool
        ]
    )
    diff = tools_diff.diff_catalogs(before, after)
    s = diff["summary"]
    assert s["added_apps"] == 1
    assert s["removed_apps"] == 1
    assert s["changed_apps"] == 1
    assert s["added_tools"] == 1
    assert s["removed_tools"] == 1


# ---------------------------------------------------------------------------
# render_diff (terminal output)
# ---------------------------------------------------------------------------


def test_render_diff_no_changes():
    cat = _catalog([_app("bot", [])])
    out = tools_diff.render_diff(tools_diff.diff_catalogs(cat, cat))
    assert "No tool-surface changes" in out


def test_render_diff_shows_added_tool():
    before = _catalog([_app("bot", [_tool("foo")])])
    after = _catalog([_app("bot", [_tool("foo"), _tool("bar")])])
    diff = tools_diff.diff_catalogs(before, after)
    out = tools_diff.render_diff(diff)
    assert "bar" in out
    assert "+ " in out  # added marker


# ---------------------------------------------------------------------------
# Snapshot/diff CLI end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_workspace(tmp_path: Path) -> Path:
    soul = tmp_path / "agents" / "bot" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("# bot\n")

    tools_dir = tmp_path / "agents" / "bot" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "list_things.py").write_text(
        "from agentino import tool\n"
        "@tool\n"
        "def list_things(status: str = 'open') -> str:\n"
        '    """List things."""\n'
        "    return ''\n"
    )

    ws_yml = tmp_path / "workspace.yml"
    ws_yml.write_text(
        yaml.safe_dump(
            {
                "name": "Test",
                "tenant_id": "test",
                "providers": {"router": {"base_url": "x", "api_key": "k"}},
                "apps": {
                    "bot": {
                        "type": "agentino",
                        "soul": "agents/bot/SOUL.md",
                        "tools": "agents/bot/tools",
                    }
                },
            }
        )
    )
    return ws_yml


def test_snapshot_writes_catalog_json(fake_workspace: Path, tmp_path: Path):
    out_path = tmp_path / "snapshot.json"
    sys.argv = ["tools_diff.py", "snapshot", str(fake_workspace), "--out", str(out_path)]
    with pytest.raises(SystemExit) as e:
        tools_diff.main()
    assert e.value.code == 0
    snapshot = json.loads(out_path.read_text())
    assert snapshot["tenant_id"] == "test"
    assert "apps" in snapshot


def test_diff_exits_0_when_unchanged(fake_workspace: Path, tmp_path: Path, capsys):
    snap_path = tmp_path / "snapshot.json"
    sys.argv = ["tools_diff.py", "snapshot", str(fake_workspace), "--out", str(snap_path)]
    with pytest.raises(SystemExit):
        tools_diff.main()
    capsys.readouterr()  # discard

    sys.argv = ["tools_diff.py", "diff", str(snap_path), str(fake_workspace), "--fail-on-change"]
    with pytest.raises(SystemExit) as e:
        tools_diff.main()
    assert e.value.code == 0
    assert "No tool-surface changes" in capsys.readouterr().out


def test_diff_exits_1_with_fail_on_change_when_tool_added(fake_workspace: Path, tmp_path: Path):
    snap_path = tmp_path / "snapshot.json"
    sys.argv = ["tools_diff.py", "snapshot", str(fake_workspace), "--out", str(snap_path)]
    with pytest.raises(SystemExit):
        tools_diff.main()

    # Add a new tool to the live workspace
    new_tool = fake_workspace.parent / "agents" / "bot" / "tools" / "new_tool.py"
    new_tool.write_text(
        "from agentino import tool\n"
        "@tool\n"
        "def new_tool(x: int = 0) -> str:\n"
        '    """New."""\n'
        "    return ''\n"
    )

    sys.argv = ["tools_diff.py", "diff", str(snap_path), str(fake_workspace), "--fail-on-change"]
    with pytest.raises(SystemExit) as e:
        tools_diff.main()
    assert e.value.code == 1


def test_diff_json_output_is_parseable(fake_workspace: Path, tmp_path: Path, capsys):
    snap_path = tmp_path / "snapshot.json"
    sys.argv = ["tools_diff.py", "snapshot", str(fake_workspace), "--out", str(snap_path)]
    with pytest.raises(SystemExit):
        tools_diff.main()
    capsys.readouterr()

    sys.argv = ["tools_diff.py", "diff", str(snap_path), str(fake_workspace), "--json"]
    with pytest.raises(SystemExit):
        tools_diff.main()
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert "summary" in parsed
    assert "apps_changed" in parsed
