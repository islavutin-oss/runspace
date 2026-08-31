"""App Registry — agents connect to the workspace like Slack apps."""

from __future__ import annotations

import contextvars
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml  # noqa: F401  (used by historical importers; preserved)

log = logging.getLogger(__name__)

# Per-request user name — set in the gateway chat/stream handlers and read
# by _build_effective_message.
request_user_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_user_name", default=None
)

# The frontend rendering contract lives in tenants/_partials/rendering_rules.md.
# An agent opts in with `{{include:../../tenants/_partials/rendering_rules.md}}`,
# which keeps the contract as data rather than framework code and lets other
# runtimes pick it up through the same include resolution. Override it per
# tenant by placing a different file beside the SOUL; opt out by omitting it.


@dataclass
class AgentApp:
    """An agent connected to the workspace."""

    id: str
    name: str
    role: str = ""
    avatar: str = "🤖"
    color: str = "#6B7280"
    group: str = "default"  # e.g. "backoffice", "customer"
    type: str = "agentino"  # agentino | http | webhook
    enabled: bool = True

    # Agentino type
    soul_path: str | None = None
    tools_dir: str | None = None
    shared_tools_dirs: list[str] = field(default_factory=list)
    # Named agentino.tools.std bundles this agent opts into (workspace.yml
    # `std_tools:`). Empty = no std tools — the agent gets ONLY its own
    # tools_dir + shared_tools_dirs + auto search_knowledge. There is no
    # implicit std-tool load; each agent declares what it needs.
    std_bundles: list[str] = field(default_factory=list)
    # Force a tool call on the first turn (agentino require_tool_use →
    # tool_choice="required"). Matches the old-prod booking agent, which
    # booked reliably because the model couldn't answer a booking request
    # in prose / hallucinate — it had to invoke a real tool. Off by default.
    require_tool_use: bool = False
    model: str | None = None

    # Gate/sanitize/security config (from workspace.yml apps section)
    gates_config: dict | None = field(default=None, repr=False)
    # Dotted path to the app's reply filter (workspace.yml
    # `response_filter: pkg.mod:callable`). None = no filter.
    response_filter: str | None = field(default=None, repr=False)
    # Cap on model turns per user message (workspace.yml `max_turns:`).
    max_turns: int = 10

    # Opening questions for this agent's 1-1 view, shown while the
    # conversation is empty. Per-agent because the workspace-level list is
    # written for whoever the workspace leads with — showing the advisor's
    # pricing questions on the catalogue desk sends people to the wrong agent.
    suggestions: list[str] = field(default_factory=list)

    # HTTP/webhook type
    endpoint: str | None = None

    # CLI-harness runtimes (codex, claude_code) — cwd for the subprocess.
    # Populated by gateway.from_config from the workspace.yml's parent dir.
    workspace_path: str | None = field(default=None, repr=False)

    # Runtime state (not persisted)
    _agent: Any = field(default=None, repr=False)
    _soul_text: str = field(default="", repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "avatar": self.avatar,
            "color": self.color,
            "group": self.group,
            "type": self.type,
            "enabled": self.enabled,
            "suggestions": self.suggestions,
        }


class ChatHistoryStore:
    """Pluggable chat history storage. Default: in-memory (single process only).

    Override get/add methods for persistent storage (e.g. Supabase, Redis).
    """

    def __init__(self, max_messages: int = 20):
        self._history: dict[str, list[dict]] = {}
        self._max = max_messages

    def get(self, session_id: str) -> list[dict]:
        return self._history.get(session_id, [])

    def add(self, session_id: str, role: str, content: str) -> None:
        self._history.setdefault(session_id, [])
        self._history[session_id].append({"role": role, "content": content})
        if len(self._history[session_id]) > self._max:
            self._history[session_id] = self._history[session_id][-self._max :]

    def clear(self, session_id: str) -> bool:
        """Drop the in-memory cache for a session. Returns True if there
        was a cache to drop, False otherwise. Subclasses persisting to
        a backing store should also clear any rows for this session.
        """
        existed = session_id in self._history
        self._history.pop(session_id, None)
        return existed


class AppRegistry:
    """Manages connected agent apps."""

    def __init__(
        self,
        workspace_name: str = "",
        user_name: str = "",
        user_role: str = "",
        tenant_id: str | None = None,
        history_store: ChatHistoryStore | None = None,
        default_provider: dict | None = None,
    ):
        self.apps: dict[str, AgentApp] = {}
        self._history_store = history_store or ChatHistoryStore()
        self.workspace_name = workspace_name
        self._user_name = user_name
        self._user_role = user_role
        self.tenant_id = tenant_id
        # Default LLM provider from workspace.yml — used when agent has no agents.yml
        self.default_provider = default_provider or {}
        # Optional callback: called before agent runs to set additional context
        # (e.g., tenant config object, sender_id). Signature: (tenant_id: str) -> None
        self.context_enricher: callable | None = None
        # Tool-usage telemetry — opt-in. When True, every chat() turn appends
        # one JSONL line per tool used to ~/.runspace/tool_usage.jsonl
        # (override path via RUNSPACE_TOOL_USAGE_PATH env var). Off by default
        # so the in-memory test path stays side-effect-free.
        self.record_tool_usage: bool = False
        # Optional callback: enrich the effective message per-agent before LLM call.
        # Signature: (app_id: str, message: str, session_id: str) -> str
        # Use this to inject agent-specific context (e.g., booking hours for booking agent only).
        self.message_enricher: callable | None = None

    def register(self, app: AgentApp) -> None:
        """Register an agent app.

        Hard-fails (FileNotFoundError) when an agentino app declares a
        `soul_path` that doesn't exist on disk. The previous behaviour
        silently skipped the load and the agent ran with the LLM's
        default persona — Ada's permission-loop incident on
        2026-05-03 was caused by exactly this: workspace.yml had
        `soul: agents/accountant/SOUL.md` resolving to a non-existent
        path, the silent skip kicked in, and her real SOUL never
        reached the model.
        """
        # SOUL flattening is runtime-agnostic — every runtime that consumes a
        # persona prompt (agentino, openclaw, codex, claude_code, pi) reads
        if app.soul_path:
            from runspace.protocols.prompt import flatten_soul as _flatten

            tenant_label = self.workspace_name.replace(" Back Office", "") or app.name
            try:
                app._soul_text = _flatten(
                    Path(app.soul_path),
                    persona_name=app.name,
                    tenant_name=tenant_label,
                )
            except FileNotFoundError as e:
                raise FileNotFoundError(f"[Registry] SOUL.md not found for agent '{app.id}': {e}")
            except ValueError as e:
                raise ValueError(
                    f"[Registry] SOUL.md for agent '{app.id}' loaded empty "
                    f"after include/template resolution: {e}"
                )

        self.apps[app.id] = app
        log.info(f"[Registry] Registered app '{app.id}' ({app.type}): {app.name}")

    def unregister(self, app_id: str) -> None:
        self.apps.pop(app_id, None)

    def get(self, app_id: str) -> AgentApp | None:
        return self.apps.get(app_id)

    def list_apps(self) -> list[dict]:
        return [a.to_dict() for a in self.apps.values() if a.enabled]

    def reload_agent(self, app_id: str) -> bool:
        """Drop the cached agentino `Agent` for this app so the next
        `chat()` call rebuilds it with fresh SOUL.md + tool code from
        disk.

        The `AgentApp` itself stays registered (id, name, role, etc
        are workspace.yml metadata; those are reloaded via
        `WorkspaceGateway.reload_config`). Only the heavyweight
        `Agent` object is cleared — its SOUL prompt template and the
        loaded tool callables are re-read on next chat.

        Returns True when an Agent was actually evicted (i.e. the
        agent had been chatted with before), False when there was
        nothing cached. Either way, subsequent chat() calls see the
        new SOUL.
        """
        app = self.apps.get(app_id)
        if not app:
            return False
        had_agent = app._agent is not None
        app._agent = None
        log.info(
            "[ReloadAgent] cleared cached Agent for %s "
            "(had_agent=%s); next chat will rebuild from disk",
            app_id,
            had_agent,
        )
        return had_agent

    def _get_history(self, session_id: str) -> list[dict]:
        return self._history_store.get(session_id)

    def _add_to_history(self, session_id: str, role: str, content: str) -> None:
        self._history_store.add(session_id, role, content)

    async def chat(self, app_id: str, message: str, session_id: str) -> dict:
        """Send a message to an app. Returns {text, tools_used}."""
        app = self.apps.get(app_id)
        if not app:
            raise ValueError(f"Unknown app: {app_id}")
        if not app.enabled:
            raise ValueError(f"App '{app_id}' is disabled")

        import time as _time

        _t0 = _time.time()

        if app.type == "agentino":
            from .runtimes import agentino as _agentino_rt

            result = await _agentino_rt.chat(self, app, message, session_id)
            self._maybe_record_tool_usage(app_id, session_id, result, _t0, _time)
            return result
        elif app.type == "codex":
            from .runtimes import codex as _codex_rt

            result = await _codex_rt.chat(self, app, message, session_id)
            self._maybe_record_tool_usage(app_id, session_id, result, _t0, _time)
            return result
        elif app.type == "claude_code":
            from .runtimes import claude_code as _cc_rt

            result = await _cc_rt.chat(self, app, message, session_id)
            self._maybe_record_tool_usage(app_id, session_id, result, _t0, _time)
            return result
        elif app.type == "openclaw":
            from .runtimes import openclaw as _oc_rt

            result = await _oc_rt.chat(self, app, message, session_id)
            self._maybe_record_tool_usage(app_id, session_id, result, _t0, _time)
            return result
        elif app.type == "pi":
            from .runtimes import pi as _pi_rt

            result = await _pi_rt.chat(self, app, message, session_id)
            self._maybe_record_tool_usage(app_id, session_id, result, _t0, _time)
            return result
        elif app.type in ("http", "webhook"):
            return await self._chat_http(app, message, session_id)
        else:
            raise ValueError(f"Unknown app type: {app.type}")

    async def chat_stream(self, app_id: str, message: str, session_id: str) -> AsyncIterator[dict]:
        """Streaming chat — yields {type: tool_call/response} events."""
        app = self.apps.get(app_id)
        if not app:
            raise ValueError(f"Unknown app: {app_id}")

        if app.type == "agentino":
            from .runtimes import agentino as _agentino_rt

            async for event in _agentino_rt.stream(self, app, message, session_id):
                yield event
        elif app.type == "codex":
            from .runtimes import codex as _codex_rt

            async for event in _codex_rt.stream(self, app, message, session_id):
                yield event
        elif app.type == "claude_code":
            from .runtimes import claude_code as _cc_rt

            async for event in _cc_rt.stream(self, app, message, session_id):
                yield event
        elif app.type == "openclaw":
            from .runtimes import openclaw as _oc_rt

            async for event in _oc_rt.stream(self, app, message, session_id):
                yield event
        elif app.type == "pi":
            from .runtimes import pi as _pi_rt

            async for event in _pi_rt.stream(self, app, message, session_id):
                yield event
        else:
            # Non-streaming fallback
            result = await self.chat(app_id, message, session_id)
            yield {
                "type": "response",
                "text": result["text"],
                "tools_used": result.get("tools_used", []),
            }

    def _maybe_record_tool_usage(
        self, app_id: str, session_id: str, result: dict, t0: float, time_mod
    ) -> None:
        """Best-effort: append tools_used to the JSONL log if telemetry is on.

        Failures are swallowed — tool-usage telemetry MUST NOT break chat().
        """
        if not self.record_tool_usage:
            return
        try:
            from .tools_usage import record_tool_calls

            record_tool_calls(
                tenant=self.tenant_id,
                agent=app_id,
                session_key=session_id,
                tools=result.get("tools_used") or [],
                turn_elapsed_ms=int((time_mod.time() - t0) * 1000),
            )
        except Exception:
            log.debug("[tool_usage] failed to record turn", exc_info=True)

    def _build_effective_message(self, message: str) -> str:
        """Prepend workspace meta to the user message via the shared contract.

        Single source of truth: `protocols.prompt.build_message_envelope`.
        Both this runtime and OpenclawRuntime call it so the same inputs
        produce the same envelope text — runtime can no longer drift on
        per-message metadata."""
        from runspace.protocols.prompt import build_message_envelope

        return build_message_envelope(
            message,
            company=self.workspace_name.replace(" Back Office", "") or None,
            user_name=request_user_name.get(None) or self._user_name or None,
            user_role=self._user_role or None,
        )

    # ── Runtime delegation shims ───────────────────────────────────
    # The agentino-specific methods live in runtimes/agentino.py so that
    # this module holds no `from agentino` imports. These shims keep the
    # underscore names working for callers that still use them.
    def _build_gate_manager(self, app: AgentApp):
        from .runtimes import agentino as _agentino_rt

        return _agentino_rt.build_gate_manager(self, app)

    def _get_or_create_agent(self, app: AgentApp):
        from .runtimes import agentino as _agentino_rt

        return _agentino_rt.get_or_create_agent(self, app)

    async def _chat_agentino(self, app: AgentApp, message: str, session_id: str) -> dict:
        from .runtimes import agentino as _agentino_rt

        return await _agentino_rt.chat(self, app, message, session_id)

    async def _stream_agentino(
        self, app: AgentApp, message: str, session_id: str
    ) -> AsyncIterator[dict]:
        from .runtimes import agentino as _agentino_rt

        async for ev in _agentino_rt.stream(self, app, message, session_id):
            yield ev

    async def _chat_http(self, app: AgentApp, message: str, session_id: str) -> dict:
        """Forward to HTTP endpoint."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                app.endpoint,
                json={
                    "message": message,
                    "session_id": session_id,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text = data.get("text") or data.get("response") or data.get("reply") or ""
            self._add_to_history(session_id, "user", message)
            self._add_to_history(session_id, "assistant", text)
            return {"text": text, "tools_used": data.get("tools_used", [])}
