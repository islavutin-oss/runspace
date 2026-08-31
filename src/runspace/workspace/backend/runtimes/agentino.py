"""Agentino runtime adapter for AppRegistry."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from .._mcp_ui import (
    begin_turn as _begin_turn,
)
from .._mcp_ui import (
    restore_mcp_ui_blocks as _restore_mcp_ui_blocks,
)
from ..response_filter import load_response_filter

if TYPE_CHECKING:  # pragma: no cover
    from ..app_registry import AgentApp, AppRegistry

log = logging.getLogger(__name__)


# Named agentino.tools.std bundles. Agents opt in via workspace.yml
# `std_tools: [documents, web]`. `all` = the whole std catalog (for agents
# that genuinely want everything). Unknown names are ignored (logged).
_STD_BUNDLES: dict[str, set[str]] = {
    "documents": {
        "create_csv",
        "create_pdf",
        "create_document",
        "create_presentation",
        "create_spreadsheet",
        "read_file",
        "list_files",
    },
    "web": {"fetch_web_data", "read_rss"},
    "weather": {"get_weather", "get_weather_forecast"},
    "memory": {"remember", "forget", "read_memory", "update_memory"},
    "translate": {"translate"},
}

# Cache the full discovered std catalog once (list of agentino Tool objects).
_STD_CATALOG_CACHE: list | None = None


def _std_catalog() -> list:
    global _STD_CATALOG_CACHE
    if _STD_CATALOG_CACHE is None:
        try:
            import agentino.tools.std as _st_pkg
            from agentino.config.tools_yaml import discover_tools_from_dir

            path = Path(_st_pkg.__path__[0])
            _STD_CATALOG_CACHE = discover_tools_from_dir(path) if path.exists() else []
        except ImportError:
            _STD_CATALOG_CACHE = []
    return _STD_CATALOG_CACHE


def _load_std_bundles(bundles: list[str]) -> list:
    """Resolve declared std-tool bundle names to concrete agentino tools."""
    catalog = _std_catalog()
    if not catalog:
        return []
    wanted: set[str] = set()
    load_all = False
    for b in bundles:
        key = str(b).strip().lower()
        if key == "all":
            load_all = True
        elif key in _STD_BUNDLES:
            wanted |= _STD_BUNDLES[key]
        else:
            log.warning("unknown std_tools bundle %r — ignored", b)
    if load_all:
        return list(catalog)
    return [t for t in catalog if getattr(t, "name", None) in wanted]


class _HistorySession:
    """Lightweight Session adapter feeding AppRegistry history into Agent.run/stream."""

    def __init__(self, history: list[dict]):
        self._history = history

    def load(self):
        from agentino.core.message import Message

        return [Message(role=m["role"], content=m["content"]) for m in self._history]

    def save(self, messages) -> None:  # noqa: ARG002
        # AppRegistry manages its own history.
        pass


def _load_memory_block(registry: AppRegistry, agent_id: str, session_id: str) -> str:
    """Durable memory block for (tenant, agent, user). Best-effort, never raises."""
    if not registry.tenant_id:
        return ""
    try:
        from agentino.tools.std._agent_memory import load_and_format_for_session
    except ImportError:
        return ""
    try:
        return load_and_format_for_session(agent_id=agent_id, user_key=session_id)
    except Exception as e:
        log.warning("memory block load failed for agent=%s session=%s: %s", agent_id, session_id, e)
        return ""


def _enrich_message(registry: AppRegistry, app_id: str, message: str, session_id: str) -> str:
    """Apply registry's optional message_enricher; swallow failures."""
    if registry.message_enricher:
        try:
            return registry.message_enricher(app_id, message, session_id)
        except Exception as e:
            log.warning("Message enricher failed for %s: %s", app_id, e)
    return message


def _set_per_turn_context(registry: AppRegistry, app: AgentApp, session_id: str):
    """Set the agentino per-turn contextvar (tenant, agent, gate manager).

    Returns the token used to reset the context, or None when no
    tenant_id is set (the registry skips context on bare/test setups).
    """
    if not registry.tenant_id:
        return None
    from agentino.core.context import set_context

    gm = build_gate_manager(registry, app)
    token = set_context(
        tenant_id=registry.tenant_id,
        sender_id=session_id,
        agent_id=app.id,
        agent_name=app.name,
        agent_role=app.role,
        _gate_manager=gm,
    )
    if registry.context_enricher:
        try:
            registry.context_enricher(registry.tenant_id)
        except Exception as e:
            log.warning("Context enricher failed: %s", e)
    return token


def _reset_context(token) -> None:
    if token is None:
        return
    from agentino.core.context import reset

    reset(token)


def build_gate_manager(registry: AppRegistry, app: AgentApp):
    """Build a fresh per-request GateManager. See app_registry doc for rationale."""
    from agentino.safety.gates import GateManager

    rules = getattr(app._agent, "_gate_rules", None) if app._agent else None
    gm = GateManager(rules=rules or [])
    if registry.tenant_id and registry.tenant_id != "default":
        gm.mark("tenant_consistent")
    log.debug(
        "[gates] built per-request GateManager for app=%s tenant=%s rules=%d "
        "tenant_consistent_marked=%s",
        app.id,
        registry.tenant_id,
        len(rules or []),
        gm.is_marked("tenant_consistent"),
    )
    return gm


def get_or_create_agent(registry: AppRegistry, app: AgentApp):
    """Lazy-create the agentino Agent for this app. Cached on app._agent."""
    if app._agent:
        return app._agent

    from agentino import Agent, load_config

    # If there's an agents.yml, use it for model/provider
    config_path = Path(app.tools_dir).parent / "agents.yml" if app.tools_dir else None
    if not config_path or not config_path.exists():
        # Check backoffice/agents.yml
        config_path = (
            Path(app.tools_dir).parents[1] / "backoffice" / "agents.yml" if app.tools_dir else None
        )

    tools: list = []
    if app.tools_dir:
        tool_path = Path(app.tools_dir)
        if tool_path.exists():
            from agentino.config.tools_yaml import discover_tools_from_dir

            tools = discover_tools_from_dir(tool_path)
    # Named std-tool bundles the agent explicitly opted into via
    # workspace.yml `std_tools:`. No implicit/blanket load — an agent with
    if getattr(app, "std_bundles", None):
        tools = tools + _load_std_bundles(app.std_bundles)
    for shared_dir in app.shared_tools_dirs:
        shared_path = Path(shared_dir)
        if shared_path.exists():
            from agentino.config.tools_yaml import discover_tools_from_dir

            tools = tools + discover_tools_from_dir(shared_path)

    dp = registry.default_provider
    base_url = dp.get("base_url")
    api_key = dp.get("api_key")
    provider = dp.get("provider")
    model = app.model or "gpt-5.4-codex"
    knowledge = None
    tmpl_tools: list = []
    fallback_models = None

    if config_path and config_path.exists():
        config = load_config(config_path)
        for tmpl in config.agents.values():
            base_url = tmpl._llm.base_url
            api_key = tmpl._llm.api_key
            provider = tmpl._llm.provider
            model = tmpl.model
            knowledge = tmpl.knowledge
            fallback_models = tmpl.fallback_models
            if tmpl.tools:
                tmpl_tools = tmpl.tools
            if hasattr(tmpl, "instructions") and tmpl.instructions:
                if not app._soul_text:
                    app._soul_text = tmpl.instructions
                elif len(tmpl.instructions) > len(app._soul_text or ""):
                    # The agents.yml declares context_files (booking flow,
                    # QA rules, templates) that agentino folds into
                    tenant_label = (getattr(registry, "workspace_name", "") or "").replace(
                        " Back Office", ""
                    ) or app.name
                    app._soul_text = tmpl.instructions.replace(
                        "{{tenant_name}}", tenant_label
                    ).replace("{{persona_name}}", app.name)
            break

    all_tools = tools or tmpl_tools

    gate_rules: list = []
    sanitize_path_params: list = []
    if app.gates_config:
        try:
            from agentino.safety.gates import GateRule

            for r in app.gates_config.get("rules", []):
                gate_rules.append(
                    GateRule(
                        gate=r["gate"],
                        tools=r["tools"],
                        message=r["message"],
                        condition=r.get("condition", ""),
                    )
                )
        except (ImportError, KeyError):
            pass
        sanitize_path_params = app.gates_config.get("sanitize", {}).get("path_args", [])

    instructions = app._soul_text or "You are a helpful assistant."

    response_filter = load_response_filter(app.response_filter)

    agent = Agent(
        model=model,
        instructions=instructions,
        tools=all_tools,
        knowledge=knowledge,
        base_url=base_url,
        api_key=api_key,
        provider=provider,
        fallback_models=fallback_models,
        max_turns=app.max_turns,
        temperature=0.7,
        sanitize_path_params=sanitize_path_params,
        require_tool_use=getattr(app, "require_tool_use", False),
        response_filter=response_filter,
    )
    agent._gate_rules = gate_rules
    app._agent = agent
    return agent


async def chat(registry: AppRegistry, app: AgentApp, message: str, session_id: str) -> dict:
    """Run an agentino agent (non-streaming)."""
    from agentino.core.message import Event, EventType

    # Call through registry._get_or_create_agent so tests that
    # monkey-patch the registry instance (set
    # `reg._get_or_create_agent = lambda app: stub`) win — the
    # registry method is a thin shim back to this module by default.
    agent_obj = registry._get_or_create_agent(app)

    effective_message = registry._build_effective_message(message)
    effective_message = _enrich_message(registry, app.id, effective_message, session_id)

    ctx_token = _set_per_turn_context(registry, app, session_id)

    memory_block = _load_memory_block(registry, app.id, session_id)
    if memory_block:
        effective_message = f"{memory_block}\n\n{effective_message}"

    tools_used: list[str] = []
    tool_outputs: list[str] = []
    prev_on_event = agent_obj.on_event

    def _capture_event(event: Event) -> None:
        if event.type == EventType.TOOL_START and event.name:
            tools_used.append(event.name)
        elif event.type == EventType.TOOL_RESULT and event.data:
            tool_outputs.append(str(event.data))
        if prev_on_event:
            prev_on_event(event)

    agent_obj.on_event = _capture_event

    # Snapshot history BEFORE adding the new user message — see
    # original AppRegistry comment for tab-switch / interrupted-request
    # rationale.
    prior_history = list(registry._get_history(session_id))
    registry._add_to_history(session_id, "user", message)
    _begin_turn()
    try:
        session = _HistorySession(prior_history) if prior_history else None
        reply = await agent_obj.run(effective_message, session=session)
    finally:
        agent_obj.on_event = prev_on_event
        _reset_context(ctx_token)

    reply = _restore_mcp_ui_blocks(reply, tool_outputs)
    registry._add_to_history(session_id, "assistant", reply)
    return {"text": reply, "tools_used": tools_used, "tool_outputs": tool_outputs}


async def stream(
    registry: AppRegistry, app: AgentApp, message: str, session_id: str
) -> AsyncIterator[dict]:
    """Streaming agentino agent — yields tool_call events then response."""
    from agentino.core.message import EventType

    # Call through registry._get_or_create_agent so tests that
    # monkey-patch the registry instance (set
    # `reg._get_or_create_agent = lambda app: stub`) win — the
    # registry method is a thin shim back to this module by default.
    agent_obj = registry._get_or_create_agent(app)

    effective_message = registry._build_effective_message(message)
    effective_message = _enrich_message(registry, app.id, effective_message, session_id)

    ctx_token = _set_per_turn_context(registry, app, session_id)

    memory_block = _load_memory_block(registry, app.id, session_id)
    if memory_block:
        effective_message = f"{memory_block}\n\n{effective_message}"

    history = registry._get_history(session_id)
    session = _HistorySession(history) if history else None

    tools_used: list[str] = []
    tool_outputs: list[str] = []
    final_text = ""

    registry._add_to_history(session_id, "user", message)
    _begin_turn()
    try:
        async for event in agent_obj.stream(effective_message, session=session):
            if event.type == EventType.TOOL_START and event.name:
                tools_used.append(event.name)
                yield {"type": "tool_call", "name": event.name}
            elif event.type == EventType.TOOL_RESULT and event.data:
                tool_outputs.append(str(event.data))
            elif event.type == EventType.TEXT and event.data:
                final_text += event.data
            elif event.type == EventType.DONE:
                if isinstance(event.data, str):
                    final_text = event.data
                elif event.data and hasattr(event.data, "content") and event.data.content:
                    final_text = event.data.content
    finally:
        _reset_context(ctx_token)

    final_text = _restore_mcp_ui_blocks(final_text, tool_outputs)
    registry._add_to_history(session_id, "assistant", final_text)
    yield {
        "type": "response",
        "text": final_text,
        "tools_used": tools_used,
        "tool_outputs": tool_outputs,
    }
