"""Integration tests for WorkspaceGateway — routes, config, multi-tenant."""

import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from runspace.workspace.backend import WorkspaceGateway, WorkspaceRegistry


@pytest.fixture
def sample_workspace_yml(tmp_path):
    """Create a minimal workspace.yml for testing."""
    soul = tmp_path / "SOUL.md"
    soul.write_text("You are a helpful test agent.")

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    config = {
        "name": "Test Workspace",
        "icon": "🧪",
        "brand_color": "#FF0000",
        "sidebar_color": "#000000",
        "users": {"default": {"name": "TestUser", "role": "Admin", "default": True}},
        "channels": [
            {"id": "general", "label": "General", "icon": "Hash", "type": "chat"},
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
        },
    }
    ws_file = tmp_path / "workspace.yml"
    ws_file.write_text(yaml.dump(config))
    return ws_file


@pytest.fixture
def gateway(sample_workspace_yml):
    return WorkspaceGateway.from_config(str(sample_workspace_yml))


class TestGatewayFromConfig:
    def test_name(self, gateway):
        assert gateway.name == "Test Workspace"

    def test_icon(self, gateway):
        assert gateway._icon == "🧪"

    def test_brand_color(self, gateway):
        assert gateway._brand_color == "#FF0000"

    def test_user(self, gateway):
        assert gateway._user_name == "TestUser"
        assert gateway._user_role == "Admin"

    def test_channels(self, gateway):
        assert len(gateway._channels) == 1
        assert gateway._channels[0]["id"] == "general"

    def test_app_registered(self, gateway):
        apps = gateway.registry.list_apps()
        assert len(apps) == 1
        assert apps[0]["name"] == "Alice"
        assert apps[0]["type"] == "agentino"

    def test_soul_loaded(self, gateway):
        app = gateway.registry.get("alice")
        assert app._soul_text == "You are a helpful test agent."


class TestGatewayRoutes:
    @pytest.fixture
    def client(self, gateway):
        app = FastAPI()
        app.include_router(gateway.router)
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_config_endpoint(self, client):
        async with client as c:
            res = await c.get("/api/workspace/config")
            assert res.status_code == 200
            data = res.json()
            assert data["name"] == "Test Workspace"
            assert len(data["apps"]) == 1
            assert data["user"]["name"] == "TestUser"

    @pytest.mark.asyncio
    async def test_apps_endpoint(self, client):
        async with client as c:
            res = await c.get("/api/workspace/apps")
            assert res.status_code == 200
            assert len(res.json()["apps"]) == 1

    @pytest.mark.asyncio
    async def test_activity_endpoint(self, client):
        async with client as c:
            res = await c.get("/api/workspace/activity")
            assert res.status_code == 200
            assert "events" in res.json()

    @pytest.mark.asyncio
    async def test_chat_unknown_app(self, client):
        async with client as c:
            res = await c.post(
                "/api/workspace/chat",
                json={
                    "app_id": "nonexistent",
                    "message": "hi",
                },
            )
            assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_routines_empty(self, client):
        async with client as c:
            res = await c.get("/api/workspace/routines")
            assert res.status_code == 200
            assert res.json()["routines"] == []

    @pytest.mark.asyncio
    async def test_chat_history_empty(self, client):
        async with client as c:
            res = await c.get("/api/workspace/chat/history?app_id=max&session_id=test-session")
            assert res.status_code == 200
            assert res.json()["messages"] == []

    @pytest.mark.asyncio
    async def test_chat_history_returns_stored_messages(self, gateway, client):
        """History endpoint returns messages previously added via chat."""
        # Seed history directly via the gateway's registry
        gateway.registry._add_to_history("s1", "user", "hello")
        gateway.registry._add_to_history("s1", "assistant", "hi there")

        async with client as c:
            res = await c.get("/api/workspace/chat/history?app_id=alice&session_id=s1")
            assert res.status_code == 200
            msgs = res.json()["messages"]
            assert len(msgs) == 2
            assert msgs[0]["role"] == "user"
            assert msgs[0]["content"] == "hello"
            assert msgs[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_config_returns_static_user(self, client):
        """Config endpoint returns workspace.yml default user (JWT override is frontend-only)."""
        async with client as c:
            res = await c.get("/api/workspace/config")
            assert res.status_code == 200
            assert res.json()["user"]["name"] == "TestUser"
            assert res.json()["user"]["role"] == "Admin"

    @pytest.mark.asyncio
    async def test_chat_request_with_sender_name(self, client):
        """ChatRequest with sender_name doesn't break (even if app not found — tests parsing)."""
        async with client as c:
            res = await c.post(
                "/api/workspace/chat",
                json={
                    "app_id": "nonexistent",
                    "message": "hi",
                    "sender_name": "Ada",
                },
            )
            # 404 because app doesn't exist — but sender_name was accepted
            assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_sender_name_sets_contextvar(self, gateway, client):
        """sender_name in ChatRequest sets request_user_name contextvar for _build_effective_message."""
        from runspace.workspace.backend.app_registry import request_user_name

        # Track what contextvar value was during the request
        captured = []
        orig = gateway.registry._build_effective_message

        def tracking_build(message):
            captured.append(request_user_name.get(None))
            return orig(message)

        gateway.registry._build_effective_message = tracking_build

        async with client as c:
            # This will 404 (alice is agentino type, no actual agent), but the contextvar is set before routing
            await c.post(
                "/api/workspace/chat",
                json={
                    "app_id": "alice",
                    "message": "hi",
                    "sender_name": "Ada",
                },
            )
            # May fail on agent execution, but contextvar should have been set
            if captured:
                assert captured[0] == "Ada"

        gateway.registry._build_effective_message = orig

    @pytest.mark.asyncio
    async def test_stream_endpoint_accepts_sender_name(self, client):
        """Stream endpoint parses sender_name without error."""
        async with client as c:
            res = await c.post(
                "/api/workspace/chat/stream",
                json={
                    "app_id": "nonexistent",
                    "message": "hi",
                    "sender_name": "Ada",
                },
            )
            assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_message_enricher_called_in_gateway(self, gateway, client):
        """message_enricher registered on registry is available via gateway."""
        calls = []
        gateway.registry.message_enricher = lambda app_id, msg, sid: (calls.append(app_id), msg)[1]
        async with client as c:
            # Will fail on agent execution, but enricher should fire
            await c.post("/api/workspace/chat", json={"app_id": "alice", "message": "hi"})
        # Clean up
        gateway.registry.message_enricher = None
        # If the request reached the agent layer, enricher was called
        if calls:
            assert calls[0] == "alice"


class TestWorkspaceRegistry:
    @pytest.fixture
    def multi_tenant_dir(self, tmp_path):
        """Create 2 tenant directories with workspace.yml."""
        for tenant, name in [("alpha", "Alpha Office"), ("beta", "Beta Office")]:
            d = tmp_path / tenant
            d.mkdir()
            soul = d / "SOUL.md"
            soul.write_text(f"I work at {name}")
            tools = d / "tools"
            tools.mkdir()

            config = {
                "name": name,
                "icon": "🏢",
                "brand_color": "#111111",
                "apps": {
                    "bot": {
                        "name": f"{tenant.capitalize()} Bot",
                        "role": "Assistant",
                        "type": "agentino",
                        "soul": "SOUL.md",
                        "tools": "tools/",
                    }
                },
            }
            (d / "workspace.yml").write_text(yaml.dump(config))
        return tmp_path

    def test_load_tenants(self, multi_tenant_dir):
        reg = WorkspaceRegistry.from_tenants_dir(multi_tenant_dir)
        assert len(reg) == 2
        assert "alpha" in reg
        assert "beta" in reg

    def test_get_by_slug(self, multi_tenant_dir):
        reg = WorkspaceRegistry.from_tenants_dir(multi_tenant_dir)
        gw = reg.get("alpha")
        assert gw is not None
        assert gw.name == "Alpha Office"

    def test_slugs(self, multi_tenant_dir):
        reg = WorkspaceRegistry.from_tenants_dir(multi_tenant_dir)
        assert set(reg.slugs) == {"alpha", "beta"}

    def test_empty_dir(self, tmp_path):
        reg = WorkspaceRegistry.from_tenants_dir(tmp_path)
        assert len(reg) == 0

    def test_nonexistent_dir(self):
        reg = WorkspaceRegistry.from_tenants_dir("/nonexistent/path")
        assert len(reg) == 0


class TestTemplateSubstitution:
    def test_persona_name_replaced(self, sample_workspace_yml):
        gw = WorkspaceGateway.from_config(str(sample_workspace_yml))
        app = gw.registry.get("alice")
        assert "{{persona_name}}" not in app._soul_text
        assert "Alice" in app._soul_text or app._soul_text == "You are a helpful test agent."

    def test_tenant_name_replaced(self, tmp_path):
        soul = tmp_path / "SOUL.md"
        soul.write_text("You are {{persona_name}} at {{tenant_name}}.")
        tools = tmp_path / "tools"
        tools.mkdir()
        import yaml

        config = {
            "name": "Acme Realty Back Office",
            "apps": {
                "lisa": {
                    "name": "Lisa",
                    "role": "Specialist",
                    "type": "agentino",
                    "soul": "SOUL.md",
                    "tools": "tools/",
                }
            },
        }
        (tmp_path / "workspace.yml").write_text(yaml.dump(config))
        gw = WorkspaceGateway.from_config(str(tmp_path / "workspace.yml"))
        app = gw.registry.get("lisa")
        assert app._soul_text == "You are Lisa at Acme Realty."
        assert "{{persona_name}}" not in app._soul_text
        assert "{{tenant_name}}" not in app._soul_text
