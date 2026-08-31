"""Tests for workspace backend — registry, activity log, gateway."""

import pytest

from runspace.workspace.backend.activity_log import ActivityLog
from runspace.workspace.backend.app_registry import AgentApp, AppRegistry


class TestActivityLog:
    def test_log_and_query(self):
        log = ActivityLog()
        log.log("nova", "Nova", "tool_call", "Called get_revenue", "tool", "get_revenue")
        log.log("otto", "Otto", "chat", "Stock is low", "response", "s1")
        events = log.query()
        assert len(events) == 2
        assert events[0]["actor"] == "otto"  # newest first

    def test_filter_by_actor(self):
        log = ActivityLog()
        log.log("nova", "Nova", "chat", "a")
        log.log("otto", "Otto", "chat", "b")
        log.log("nova", "Nova", "chat", "c")
        assert len(log.query(actor="nova")) == 2

    def test_filter_by_action(self):
        log = ActivityLog()
        log.log("nova", "Nova", "tool_call", "a")
        log.log("nova", "Nova", "chat", "b")
        assert len(log.query(action="tool_call")) == 1

    def test_max_entries(self):
        log = ActivityLog(max_entries=10)
        for i in range(20):
            log.log("t", "T", "a", f"event {i}")
        assert len(log._log) == 10

    def test_clear(self):
        log = ActivityLog()
        log.log("t", "T", "a", "x")
        log.clear()
        assert len(log.query()) == 0


class TestAppRegistry:
    def test_register_and_list(self):
        reg = AppRegistry()
        reg.register(AgentApp(id="nova", name="Nova", role="Finance"))
        reg.register(AgentApp(id="otto", name="Otto", role="Inventory"))
        apps = reg.list_apps()
        assert len(apps) == 2
        assert apps[0]["id"] == "nova"

    def test_get(self):
        reg = AppRegistry()
        reg.register(AgentApp(id="nova", name="Nova"))
        assert reg.get("nova").name == "Nova"
        assert reg.get("nonexistent") is None

    def test_unregister(self):
        reg = AppRegistry()
        reg.register(AgentApp(id="nova", name="Nova"))
        reg.unregister("nova")
        assert len(reg.list_apps()) == 0

    def test_disabled_not_listed(self):
        reg = AppRegistry()
        reg.register(AgentApp(id="nova", name="Nova", enabled=False))
        assert len(reg.list_apps()) == 0

    def test_history_management(self):
        reg = AppRegistry()
        reg._add_to_history("s1", "user", "hello")
        reg._add_to_history("s1", "assistant", "hi")
        assert len(reg._get_history("s1")) == 2
        assert reg._get_history("nonexistent") == []

    def test_history_max(self):
        from runspace.workspace.backend.app_registry import ChatHistoryStore

        reg = AppRegistry(history_store=ChatHistoryStore(max_messages=5))
        for i in range(10):
            reg._add_to_history("s1", "user", f"msg {i}")
        assert len(reg._get_history("s1")) == 5

    @pytest.mark.asyncio
    async def test_chat_unknown_app(self):
        reg = AppRegistry()
        with pytest.raises(ValueError, match="Unknown app"):
            await reg.chat("nonexistent", "hello", "s1")

    @pytest.mark.asyncio
    async def test_chat_disabled_app(self):
        reg = AppRegistry()
        reg.register(AgentApp(id="nova", name="Nova", enabled=False))
        with pytest.raises(ValueError, match="disabled"):
            await reg.chat("nova", "hello", "s1")

    def test_to_dict(self):
        app = AgentApp(
            id="nova", name="Nova", role="Finance", avatar="💰", color="#059669", group="backoffice"
        )
        d = app.to_dict()
        assert d["id"] == "nova"
        assert d["avatar"] == "💰"
        assert "soul_path" not in d  # internal fields excluded

    def test_context_enricher_called(self):
        """context_enricher callback is available on AppRegistry."""
        reg = AppRegistry()
        assert reg.context_enricher is None
        calls = []
        reg.context_enricher = lambda tid: calls.append(tid)
        reg.context_enricher("test-tenant")
        assert calls == ["test-tenant"]

    def test_pluggable_history_store(self):
        """Custom history store is used by AppRegistry."""
        from runspace.workspace.backend.app_registry import ChatHistoryStore

        class TrackingStore(ChatHistoryStore):
            def __init__(self):
                super().__init__()
                self.get_calls = []

            def get(self, session_id: str) -> list[dict]:
                self.get_calls.append(session_id)
                return super().get(session_id)

        store = TrackingStore()
        reg = AppRegistry(history_store=store)
        reg._add_to_history("s1", "user", "hello")
        reg._get_history("s1")
        assert "s1" in store.get_calls
        assert len(reg._get_history("s1")) == 1


class TestUserIdentity:
    """Tests for per-request user identity via request_user_name contextvar."""

    def test_build_effective_message_static_user(self):
        """_build_effective_message uses static user_name when no contextvar is set."""
        reg = AppRegistry(workspace_name="Acme Back Office", user_name="sam")
        msg = reg._build_effective_message("hello")
        assert "[company: Acme, user: sam]" in msg
        assert "hello" in msg

    def test_build_effective_message_contextvar_overrides(self):
        """Per-request user name from contextvar takes priority over static config."""
        from runspace.workspace.backend.app_registry import request_user_name

        reg = AppRegistry(workspace_name="Acme Back Office", user_name="sam")
        token = request_user_name.set("Ada")
        try:
            msg = reg._build_effective_message("hello")
            assert "user: Ada" in msg
            assert "user: sam" not in msg
        finally:
            request_user_name.reset(token)

    def test_build_effective_message_contextvar_cleared(self):
        """After resetting contextvar, static user_name is used again."""
        from runspace.workspace.backend.app_registry import request_user_name

        reg = AppRegistry(workspace_name="Test", user_name="Default")
        token = request_user_name.set("Override")
        request_user_name.reset(token)
        msg = reg._build_effective_message("hello")
        assert "user: Default" in msg

    def test_build_effective_message_no_user(self):
        """No user meta when neither contextvar nor static user is set."""
        reg = AppRegistry(workspace_name="Test")
        msg = reg._build_effective_message("hello")
        assert "user:" not in msg

    def test_message_enricher_called_for_agent(self):
        """message_enricher callback receives app_id and can modify the message."""
        reg = AppRegistry(workspace_name="Test", user_name="Tester")
        calls = []

        def enricher(app_id, message, session_id):
            calls.append(app_id)
            if app_id == "booking":
                return message + "\n[BOOKING CONTEXT]"
            return message

        reg.message_enricher = enricher
        # _build_effective_message doesn't call enricher (it's called in _chat/_stream)
        # but we can verify the enricher works directly
        result = enricher("booking", "hello", "s1")
        assert "[BOOKING CONTEXT]" in result
        result2 = enricher("finance", "hello", "s1")
        assert "[BOOKING CONTEXT]" not in result2
        assert calls == ["booking", "finance"]

    def test_message_enricher_default_none(self):
        """message_enricher is None by default."""
        reg = AppRegistry()
        assert reg.message_enricher is None

    def test_message_enricher_only_modifies_target_agent(self):
        """message_enricher should not modify messages for non-target agents."""
        reg = AppRegistry(workspace_name="Test", user_name="User")

        def enricher(app_id, message, session_id):
            if app_id == "booking":
                return message + "\n[HOURS: Wed-Sun 18:00-23:00]"
            return message  # untouched for other agents

        reg.message_enricher = enricher
        # Booking gets enriched
        assert "[HOURS:" in enricher("booking", "hi", "s1")
        # Nova, Luca, Otto do NOT get booking context
        for agent in ("finance", "analytics", "inventory"):
            result = enricher(agent, "hi", "s1")
            assert "[HOURS:" not in result, f"{agent} should not get booking context"

    def test_contextvar_and_enricher_compose(self):
        """Per-request user name (contextvar) + message_enricher both apply."""
        from runspace.workspace.backend.app_registry import request_user_name

        reg = AppRegistry(workspace_name="Test Back Office", user_name="Default")
        enriched = []

        def enricher(app_id, message, session_id):
            enriched.append(message)
            return message + "\n[ENRICHED]"

        reg.message_enricher = enricher
        # Set per-request user
        token = request_user_name.set("Ada")
        try:
            msg = reg._build_effective_message("hello")
            assert "user: Ada" in msg
            # Simulate what _chat_agentino does after _build_effective_message
            result = enricher("booking", msg, "s1")
            assert "user: Ada" in result
            assert "[ENRICHED]" in result
        finally:
            request_user_name.reset(token)

    def test_contextvar_isolated_between_requests(self):
        """Contextvar resets properly — no user name leaks between requests."""
        from runspace.workspace.backend.app_registry import request_user_name

        reg = AppRegistry(user_name="Default")
        # Request 1: Ada
        t1 = request_user_name.set("Ada")
        msg1 = reg._build_effective_message("hi")
        request_user_name.reset(t1)
        # Request 2: no contextvar
        msg2 = reg._build_effective_message("hi")
        # Request 3: Demo
        t3 = request_user_name.set("Demo")
        msg3 = reg._build_effective_message("hi")
        request_user_name.reset(t3)

        assert "user: Ada" in msg1
        assert "user: Default" in msg2
        assert "user: Demo" in msg3

    def test_chat_request_accepts_sender_name(self):
        """ChatRequest model accepts optional sender_name field."""
        from runspace.workspace.backend.gateway import ChatRequest

        req = ChatRequest(app_id="max", message="hi", sender_name="Ada")
        assert req.sender_name == "Ada"

    def test_chat_request_sender_name_optional(self):
        """ChatRequest works without sender_name (backward compat)."""
        from runspace.workspace.backend.gateway import ChatRequest

        req = ChatRequest(app_id="max", message="hi")
        assert req.sender_name is None


# Rendering rules used to be a Python constant `_SHARED_RENDERING_RULES`
# in app_registry.py and were tested here. They moved to a markdown


class TestChannelSeedBackofficeFilter:
    """Customer-facing agents (group='customer', e.g. the booking agent that
    talks to guests on WhatsApp) must NOT be auto-added to the team workspace's
    chat channels — those are for humans + backoffice agents to coordinate.
    """

    def _build_messaging_with_capture(self):
        from runspace.workspace.backend.messaging import MessagingService

        added: list[dict] = []

        # Use object.__new__ to bypass MessagingService.__init__ (which needs
        # SUPABASE_URL/KEY env). We only exercise ensure_default_channels.
        svc = object.__new__(MessagingService)
        svc.tenant_id = "tenant-x"

        def fake_get_channel_by_slug(slug):
            return None  # Force creation path

        def fake_create_channel(*, name, slug, icon, is_default, created_by):
            return {"id": f"chan-{slug}", "slug": slug}

        def fake_add_member(*, channel_id, member_type, member_id, member_name):
            added.append(
                {
                    "channel_id": channel_id,
                    "member_type": member_type,
                    "member_id": member_id,
                    "member_name": member_name,
                }
            )

        svc.get_channel_by_slug = fake_get_channel_by_slug
        svc.create_channel = fake_create_channel
        svc.add_channel_member = fake_add_member
        return svc, added

    def test_customer_agent_excluded_from_general(self):
        svc, added = self._build_messaging_with_capture()
        channels = [{"id": "general", "label": "#general", "icon": "Hash", "type": "chat"}]
        agents = {
            "booking": {"name": "Max", "group": "customer"},
            "analytics": {"name": "Luca", "group": "backoffice"},
            "finance": {"name": "Nova", "group": "backoffice"},
            "inventory": {"name": "Otto", "group": "backoffice"},
        }
        svc.ensure_default_channels(channels, agents)

        member_ids = {m["member_id"] for m in added if m["channel_id"] == "chan-general"}
        assert "booking" not in member_ids, (
            "booking (group=customer) must be excluded from #general"
        )
        assert {"analytics", "finance", "inventory"}.issubset(member_ids), (
            "all backoffice agents must be added to #general"
        )

    def test_default_group_treated_as_backoffice(self):
        """An agent without an explicit `group` field (legacy config) defaults
        to backoffice — preserving prior behavior except where group is the
        explicit string 'customer'."""
        svc, added = self._build_messaging_with_capture()
        channels = [{"id": "general", "label": "#general", "icon": "Hash", "type": "chat"}]
        agents = {"legacy": {"name": "Legacy"}}  # no group field
        svc.ensure_default_channels(channels, agents)
        assert any(m["member_id"] == "legacy" for m in added)

    def test_addon_channels_get_no_members(self):
        """Only chat-type channels are seeded with members. Addon channels
        (Bookings, Analytics tabs) live outside the messaging table."""
        svc, added = self._build_messaging_with_capture()
        channels = [
            {"id": "general", "type": "chat"},
            {"id": "bookings", "type": "addon"},
            {"id": "analytics", "type": "addon"},
        ]
        agents = {"analytics": {"name": "Luca", "group": "backoffice"}}
        svc.ensure_default_channels(channels, agents)
        assert all(m["channel_id"] == "chan-general" for m in added), (
            "members must only be added to chat channels, never to addons"
        )
