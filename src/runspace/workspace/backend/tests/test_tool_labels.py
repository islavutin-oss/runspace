"""Reader-facing tool names.

Tool names are written for the model — `run_sql`, `top_sellers` — and the
chat shows them while a turn runs and in the "Used:" line under the reply.
`tool_labels:` on an app in workspace.yml gives each one a name a person
should read instead. The label rides on the events; the name stays the
identifier in the activity log.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runspace.workspace.backend.app_registry import AgentApp
from runspace.workspace.backend.gateway import WorkspaceGateway

LABELS = {"run_sql": "SQL query", "top_sellers": "Top sellers"}


def _gateway(labels: dict[str, str] | None = LABELS) -> WorkspaceGateway:
    gw = WorkspaceGateway(name="Test")
    gw.registry.default_provider = {"base_url": "http://x", "api_key": "k", "provider": "openai"}
    gw.registry.register(
        AgentApp(id="ana", name="Ana", type="agentino", tool_labels=dict(labels or {}))
    )

    # Stub the runtime rather than chat_stream itself: labelling lives in
    # chat_stream, so a test that replaced it would assert against its own
    # stub — which is how the unlabelled path reached an application.
    from runspace.workspace.backend.runtimes import agentino as _rt

    async def fake_stream(registry, app, message, session_id) -> AsyncIterator[dict]:
        registry._add_to_history(session_id, "user", message)
        yield {"type": "tool_call", "name": "top_sellers"}
        yield {"type": "tool_call", "name": "run_sql"}
        yield {"type": "tool_call", "name": "describe"}  # no label
        registry._add_to_history(session_id, "assistant", "done")
        yield {
            "type": "response",
            "text": "done",
            "tools_used": ["top_sellers", "run_sql", "run_sql", "describe"],
        }

    _rt.stream = fake_stream  # type: ignore[assignment]
    return gw


def _events(gw: WorkspaceGateway) -> list[dict]:
    app = FastAPI()
    app.include_router(gw.router)
    with TestClient(app) as client:
        r = client.post(
            "/api/workspace/chat/stream",
            json={"app_id": "ana", "message": "hi", "session_id": "s1"},
        )
        assert r.status_code == 200
        return [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]


def test_tool_call_events_carry_the_label_when_there_is_one():
    calls = [e for e in _events(_gateway()) if e["type"] == "tool_call"]
    assert [(e["name"], e.get("label")) for e in calls] == [
        ("top_sellers", "Top sellers"),
        ("run_sql", "SQL query"),
        ("describe", None),
    ]


def test_the_response_carries_labels_for_the_tools_it_used():
    resp = [e for e in _events(_gateway()) if e["type"] == "response"][0]
    # names stay the record; labels are a lookup beside them
    assert resp["tools_used"] == ["top_sellers", "run_sql", "run_sql", "describe"]
    assert resp["tool_labels"] == LABELS


def test_no_labels_means_no_label_fields():
    events = _events(_gateway(labels=None))
    assert all("label" not in e for e in events if e["type"] == "tool_call")
    assert "tool_labels" not in [e for e in events if e["type"] == "response"][0]


def test_activity_log_reads_the_label_but_files_under_the_name():
    gw = _gateway()
    _events(gw)
    calls = gw.activity.query(limit=50, action="tool_call")
    by_id = {e["entity_id"]: e["detail"] for e in calls}
    assert by_id["top_sellers"] == "Called Top sellers"
    assert by_id["describe"] == "Called describe"


def test_labels_come_from_workspace_yml_and_reach_the_apps_list(tmp_path):
    (tmp_path / "SOUL.md").write_text("You are Ana.")
    (tmp_path / "workspace.yml").write_text(
        yaml.safe_dump(
            {
                "name": "Test",
                "apps": {
                    "ana": {
                        "name": "Ana",
                        "soul": "SOUL.md",
                        "tool_labels": {"run_sql": "SQL query", "empty": ""},
                    },
                    "bob": {"name": "Bob", "soul": "SOUL.md"},
                },
            }
        )
    )
    gw = WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))
    ana = gw.registry.get("ana")
    assert ana.tool_labels == {"run_sql": "SQL query"}  # an empty label is no label
    assert gw.registry.get("bob").tool_labels == {}
    # The UI resolves labels for persisted messages from the apps list.
    assert ana.to_dict()["tool_labels"] == {"run_sql": "SQL query"}


def test_a_malformed_tool_labels_value_is_ignored(tmp_path):
    (tmp_path / "SOUL.md").write_text("You are Ana.")
    (tmp_path / "workspace.yml").write_text(
        yaml.safe_dump(
            {"name": "T", "apps": {"ana": {"name": "Ana", "soul": "SOUL.md", "tool_labels": ["x"]}}}
        )
    )
    gw = WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))
    assert gw.registry.get("ana").tool_labels == {}


def test_a_consumer_of_chat_stream_gets_the_same_labels():
    """An application may wrap `chat_stream` and serve the events itself —
    adding its own guards on the way past — rather than mounting the gateway's
    route. It gets the labels too; when it did not, the same workspace showed
    tool identifiers on one deployment and readable names on another."""
    import asyncio

    gw = _gateway()

    async def collect() -> list[dict]:
        return [e async for e in gw.registry.chat_stream("ana", "hi", "s-direct")]

    events = asyncio.run(collect())
    calls = [(e["name"], e.get("label")) for e in events if e["type"] == "tool_call"]
    assert calls == [
        ("top_sellers", "Top sellers"),
        ("run_sql", "SQL query"),
        ("describe", None),
    ]
    assert events[-1]["tool_labels"] == LABELS
