"""Advanced tests for workspace backend — activity log, app registry, templates, gateway, registry."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from runspace.workspace.backend import (
    ActivityLog,
    AgentApp,
    AppRegistry,
    WorkspaceGateway,
    WorkspaceRegistry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_workspace_yml(tmp_path):
    """Minimal workspace.yml with one enabled app and one disabled app."""
    soul = tmp_path / "SOUL.md"
    soul.write_text("You are {{persona_name}}, assistant for {{tenant_name}}.")

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    config = {
        "name": "Acme Corp Back Office",
        "icon": "🏢",
        "brand_color": "#123456",
        "sidebar_color": "#654321",
        "users": {
            "admin": {"name": "Admin", "role": "Owner", "default": True},
            "viewer": {"name": "Viewer", "role": "Read-only"},
        },
        "channels": [
            {"id": "general", "label": "General", "icon": "Hash", "type": "chat"},
            {"id": "alerts", "label": "Alerts", "icon": "Bell", "type": "feed"},
        ],
        "apps": {
            "alice": {
                "name": "Alice",
                "role": "Assistant",
                "avatar": "🤖",
                "color": "#0000FF",
                "group": "backoffice",
                "type": "agentino",
                "soul": "SOUL.md",
                "tools": "tools/",
            },
            "disabled_bot": {
                "name": "Disabled Bot",
                "role": "Hidden",
                "type": "agentino",
                "enabled": False,
                "soul": "SOUL.md",
                "tools": "tools/",
            },
        },
    }
    ws_file = tmp_path / "workspace.yml"
    ws_file.write_text(yaml.dump(config))
    return ws_file


@pytest.fixture
def gateway(sample_workspace_yml):
    return WorkspaceGateway.from_config(str(sample_workspace_yml))


@pytest.fixture
def test_app(gateway):
    """FastAPI app with the gateway router mounted."""
    app = FastAPI()
    app.include_router(gateway.router)
    return app


@pytest.fixture
def client(test_app):
    transport = ASGITransport(app=test_app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# ActivityLog Advanced
# ---------------------------------------------------------------------------


class TestActivityLogAdvanced:
    def test_activity_log_includes_timestamp(self):
        log = ActivityLog()
        entry = log.log("bot", "Bot", "chat", "hello")
        assert hasattr(entry, "timestamp")
        assert isinstance(entry.timestamp, float)
        assert entry.timestamp > 0
        # Also check the dict representation
        events = log.query(limit=1)
        assert "timestamp" in events[0]
        assert "time_iso" in events[0]

    def test_activity_log_newest_first(self):
        log = ActivityLog()
        log.log("a", "A", "chat", "first")
        time.sleep(0.01)
        log.log("b", "B", "chat", "second")
        time.sleep(0.01)
        log.log("c", "C", "chat", "third")
        events = log.query()
        assert events[0]["actor"] == "c"
        assert events[1]["actor"] == "b"
        assert events[2]["actor"] == "a"

    @pytest.mark.asyncio
    async def test_activity_log_concurrent_logging(self):
        log = ActivityLog()
        n_tasks = 20

        async def log_event(i):
            # ActivityLog.log is sync, but we run many tasks concurrently
            log.log(f"actor_{i}", f"Actor {i}", "chat", f"message {i}")

        await asyncio.gather(*(log_event(i) for i in range(n_tasks)))
        events = log.query(limit=100)
        assert len(events) == n_tasks
        # All actors present
        actors = {e["actor"] for e in events}
        assert actors == {f"actor_{i}" for i in range(n_tasks)}


# ---------------------------------------------------------------------------
# AppRegistry Tool/Chat
# ---------------------------------------------------------------------------


class TestAppRegistryChat:
    @pytest.mark.asyncio
    async def test_chat_unknown_app_raises(self):
        reg = AppRegistry()
        with pytest.raises(ValueError, match="Unknown app"):
            await reg.chat("nonexistent", "hi", "s1")

    @pytest.mark.asyncio
    async def test_chat_disabled_app_raises(self):
        reg = AppRegistry()
        reg.register(AgentApp(id="bot", name="Bot", enabled=False))
        with pytest.raises(ValueError, match="disabled"):
            await reg.chat("bot", "hi", "s1")

    def test_history_isolated_per_session(self):
        reg = AppRegistry()
        reg._add_to_history("session_a", "user", "hello from A")
        reg._add_to_history("session_a", "assistant", "reply to A")
        reg._add_to_history("session_b", "user", "hello from B")

        hist_a = reg._get_history("session_a")
        hist_b = reg._get_history("session_b")

        assert len(hist_a) == 2
        assert len(hist_b) == 1
        assert hist_a[0]["content"] == "hello from A"
        assert hist_b[0]["content"] == "hello from B"

    def test_agent_app_to_dict_excludes_internals(self):
        app = AgentApp(
            id="bot",
            name="Bot",
            role="Helper",
            soul_path="/some/path.md",
            tools_dir="/some/tools",
        )
        app._agent = "fake_agent_object"
        app._soul_text = "some soul text"

        d = app.to_dict()
        assert "soul_path" not in d
        assert "tools_dir" not in d
        assert "_agent" not in d
        assert "_soul_text" not in d
        # Positive check: public fields present
        assert d["id"] == "bot"
        assert d["name"] == "Bot"
        assert d["role"] == "Helper"


# ---------------------------------------------------------------------------
# Template Substitution
# ---------------------------------------------------------------------------


class TestTemplateSubstitutionAdvanced:
    def test_persona_name_all_occurrences_replaced(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text(
            "Hello I am {{persona_name}}. "
            "People call me {{persona_name}}. "
            "Signed, {{persona_name}}."
        )
        tools = tmp_path / "tools"
        tools.mkdir()
        config = {
            "name": "TestCo",
            "apps": {
                "zoe": {"name": "Zoe", "type": "agentino", "soul": "SOUL.md", "tools": "tools/"}
            },
        }
        (tmp_path / "workspace.yml").write_text(yaml.dump(config))
        gw = WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))
        app = gw.registry.get("zoe")
        assert "{{persona_name}}" not in app._soul_text
        assert app._soul_text.count("Zoe") == 3

    def test_tenant_name_strips_back_office(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("Welcome to {{tenant_name}}.")
        tools = tmp_path / "tools"
        tools.mkdir()
        config = {
            "name": "Foo Bar Back Office",
            "apps": {
                "bot": {"name": "Bot", "type": "agentino", "soul": "SOUL.md", "tools": "tools/"}
            },
        }
        (tmp_path / "workspace.yml").write_text(yaml.dump(config))
        gw = WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))
        app = gw.registry.get("bot")
        assert app._soul_text == "Welcome to Foo Bar."
        assert "Back Office" not in app._soul_text

    def test_no_template_vars_when_none_in_soul(self, tmp_path):
        original = "I am a plain soul with no variables at all."
        soul = tmp_path / "SOUL.md"
        soul.write_text(original)
        tools = tmp_path / "tools"
        tools.mkdir()
        config = {
            "name": "Plain Co",
            "apps": {
                "bot": {"name": "Bot", "type": "agentino", "soul": "SOUL.md", "tools": "tools/"}
            },
        }
        (tmp_path / "workspace.yml").write_text(yaml.dump(config))
        gw = WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))
        app = gw.registry.get("bot")
        assert app._soul_text == original

    def test_special_chars_in_names_safe(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("I am {{persona_name}} at {{tenant_name}}.")
        tools = tmp_path / "tools"
        tools.mkdir()
        config = {
            "name": "O'Brien & Associates Back Office",
            "apps": {
                "bot": {
                    "name": "D'Artagnan & Co",
                    "type": "agentino",
                    "soul": "SOUL.md",
                    "tools": "tools/",
                }
            },
        }
        (tmp_path / "workspace.yml").write_text(yaml.dump(config))
        gw = WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))
        app = gw.registry.get("bot")
        assert app._soul_text == "I am D'Artagnan & Co at O'Brien & Associates."
        assert "{{persona_name}}" not in app._soul_text
        assert "{{tenant_name}}" not in app._soul_text


# ---------------------------------------------------------------------------
# WorkspaceGateway Config Loading
# ---------------------------------------------------------------------------


class TestGatewayConfigLoading:
    def test_missing_apps_returns_empty_list(self, tmp_path):
        config = {"name": "Empty"}
        (tmp_path / "workspace.yml").write_text(yaml.dump(config))
        gw = WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))
        assert gw.registry.list_apps() == []

    def test_missing_channels_returns_empty(self, tmp_path):
        config = {"name": "No Channels"}
        (tmp_path / "workspace.yml").write_text(yaml.dump(config))
        gw = WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))
        assert gw._channels == []

    def test_disabled_app_not_in_list(self, gateway):
        apps = gateway.registry.list_apps()
        app_ids = [a["id"] for a in apps]
        assert "disabled_bot" not in app_ids
        # But it still exists in the registry internally
        assert gateway.registry.get("disabled_bot") is not None

    def test_multiple_users_default_selected(self, gateway):
        assert gateway._user_name == "Admin"
        assert gateway._user_role == "Owner"


# ---------------------------------------------------------------------------
# WorkspaceRegistry
# ---------------------------------------------------------------------------


class TestWorkspaceRegistryAdvanced:
    @pytest.fixture
    def registry_with_two(self, tmp_path):
        """Registry with two workspaces: alpha and beta."""
        for slug, name in [("alpha", "Alpha Office"), ("beta", "Beta Office")]:
            d = tmp_path / slug
            d.mkdir()
            soul = d / "SOUL.md"
            soul.write_text(f"I work at {name}")
            tools = d / "tools"
            tools.mkdir()
            config = {
                "name": name,
                "apps": {
                    "bot": {
                        "name": f"{slug.capitalize()} Bot",
                        "role": "Assistant",
                        "type": "agentino",
                        "soul": "SOUL.md",
                        "tools": "tools/",
                    }
                },
            }
            (d / "workspace.yml").write_text(yaml.dump(config))
        return WorkspaceRegistry.from_tenants_dir(tmp_path)

    def _make_request(self, headers: dict) -> Request:
        """Create a fake Starlette Request with given headers."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
        return Request(scope)

    def test_resolve_returns_default_for_unknown_host(self, registry_with_two):
        req = self._make_request({"host": "unknown.example.com"})
        gw = registry_with_two.resolve(req)
        assert gw is not None
        # Should return the first workspace (alpha, since sorted)
        first_slug = registry_with_two.slugs[0]
        assert gw is registry_with_two.get(first_slug)

    def test_resolve_checks_referer_fallback(self, registry_with_two):
        req = self._make_request(
            {
                "host": "unknown.example.com",
                "referer": "https://app.example.com/beta/dashboard",
            }
        )
        gw = registry_with_two.resolve(req)
        assert gw is not None
        assert gw.name == "Beta Office"

    def test_custom_slug_fn(self, tmp_path):
        """Custom slug_fn extracts slug differently (e.g. full dir name)."""
        d = tmp_path / "my-tenant-123"
        d.mkdir()
        soul = d / "SOUL.md"
        soul.write_text("custom slug test")
        tools = d / "tools"
        tools.mkdir()
        config = {
            "name": "Custom",
            "apps": {
                "bot": {"name": "Bot", "type": "agentino", "soul": "SOUL.md", "tools": "tools/"}
            },
        }
        (d / "workspace.yml").write_text(yaml.dump(config))

        # Default slug_fn splits on '-', so slug would be "my"
        reg_default = WorkspaceRegistry.from_tenants_dir(tmp_path)
        assert "my" in reg_default.slugs

        # Custom slug_fn uses full dir name
        reg_custom = WorkspaceRegistry.from_tenants_dir(tmp_path, slug_fn=lambda name: name.upper())
        assert "MY-TENANT-123" in reg_custom.slugs


# ---------------------------------------------------------------------------
# Frontend-Backend Contract (response shapes)
# ---------------------------------------------------------------------------


class TestFrontendBackendContract:
    @pytest.mark.asyncio
    async def test_config_response_has_required_fields(self, client):
        async with client as c:
            res = await c.get("/api/workspace/config")
            assert res.status_code == 200
            data = res.json()
            for field in ("name", "apps", "channels", "user", "brand_color", "icon"):
                assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_apps_response_has_id_name_role(self, client):
        async with client as c:
            res = await c.get("/api/workspace/apps")
            assert res.status_code == 200
            apps = res.json()["apps"]
            assert len(apps) >= 1
            for app in apps:
                for field in ("id", "name", "role", "type"):
                    assert field in app, f"Missing field in app: {field}"

    @pytest.mark.asyncio
    async def test_activity_response_is_list(self, client, gateway):
        # Seed some activity
        gateway.activity.log("bot", "Bot", "chat", "test event")
        async with client as c:
            res = await c.get("/api/workspace/activity")
            assert res.status_code == 200
            data = res.json()
            assert "events" in data
            assert isinstance(data["events"], list)

    @pytest.mark.asyncio
    async def test_chat_response_has_app_id_response(self, client, gateway):
        # Mock the registry.chat to avoid needing a real LLM
        mock_result = {"text": "Hello from mock!", "tools_used": ["search"]}
        with patch.object(
            gateway.registry, "chat", new_callable=AsyncMock, return_value=mock_result
        ):
            async with client as c:
                res = await c.post(
                    "/api/workspace/chat",
                    json={
                        "app_id": "alice",
                        "message": "hello",
                        "session_id": "test-session",
                    },
                )
                assert res.status_code == 200
                data = res.json()
                assert "app_id" in data
                assert data["app_id"] == "alice"
                assert "response" in data
                assert data["response"] == "Hello from mock!"
                assert "session_id" in data
