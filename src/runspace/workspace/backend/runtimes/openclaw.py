"""OpenClaw runtime adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..app_registry import AgentApp, AppRegistry

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = float(os.environ.get("RUNSPACE_CLI_TIMEOUT", "120"))
OPENCLAW_BIN_ENV = "OPENCLAW_BIN"
OPENCLAW_BIN_DEFAULT = "openclaw"
DEFAULT_AGENT_ID = "main"
DEFAULT_MODEL = "openai-codex/gpt-5.4-codex"


def get_or_create_agent(registry: AppRegistry, app: AgentApp):  # noqa: ARG001
    if app._agent is None:
        app._agent = {"runtime": "openclaw"}
    return app._agent


def build_gate_manager(registry: AppRegistry, app: AgentApp):  # noqa: ARG001
    return None


def _resolve_bin() -> str:
    return (
        os.environ.get(OPENCLAW_BIN_ENV)
        or shutil.which(OPENCLAW_BIN_DEFAULT)
        or OPENCLAW_BIN_DEFAULT
    )


def _resolve_profile(registry: AppRegistry, app: AgentApp) -> str:
    cfg = app.gates_config or {}
    if cfg.get("openclaw_profile"):
        return str(cfg["openclaw_profile"])
    if registry.tenant_id:
        return registry.tenant_id
    return "default"


def _resolve_agent_id(app: AgentApp) -> str:
    cfg = app.gates_config or {}
    return cfg.get("openclaw_agent") or DEFAULT_AGENT_ID


def _resolve_model(app: AgentApp) -> str:
    return app.model or DEFAULT_MODEL


def _build_message(registry: AppRegistry, app: AgentApp, message: str, session_id: str) -> str:
    """Render SOUL + history + envelope into a single message string.

    OpenClaw `agent --local --message <text>` takes one positional message
    blob; we prepend the persona prompt + prior turns so the model has
    context, mirroring how codex/claude_code adapters build their prompts.
    """
    from runspace.protocols.prompt import build_message_envelope

    parts: list[str] = []
    if app._soul_text:
        parts.append(app._soul_text.strip())

    history = registry._get_history(session_id)
    if history:
        parts.append("## Conversation so far")
        for turn in history:
            role = turn.get("role", "user").upper()
            content = (turn.get("content") or "").strip()
            if content:
                parts.append(f"[{role}]\n{content}")

    envelope = build_message_envelope(
        message,
        company=registry.workspace_name.replace(" Back Office", "") or None,
        user_name=registry._user_name or None,
        user_role=registry._user_role or None,
    )
    parts.append("## Current request")
    parts.append(envelope)
    return "\n\n".join(parts)


def _parse_openclaw_json(stdout: str) -> tuple[str, list[str], list[str]]:
    """Walk `openclaw agent --json` output. Returns (text, tools, outputs).

    OpenClaw 2026.5.x emits a single top-level JSON object containing
    `result` (or `assistantMessage.content`), with `toolCalls[]` listing
    each invoked tool name and result payload. Defensive against shape
    drift across point releases.
    """
    text = ""
    tools_used: list[str] = []
    tool_outputs: list[str] = []

    # Stdout may be a single JSON object or a JSON-Lines stream of events.
    # Try whole-blob first; on failure, line-by-line.
    candidates: list[dict] = []
    s = stdout.strip()
    if not s:
        return "", [], []
    try:
        d = json.loads(s)
        candidates = [d] if isinstance(d, dict) else []
    except json.JSONDecodeError:
        for line in s.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                candidates.append(ev)

    for d in candidates:
        # Real 2026.5.4 shape: {payloads:[{text,mediaUrl}], meta:{agentMeta:{...}}}
        payloads = d.get("payloads")
        if isinstance(payloads, list) and not text:
            pieces: list[str] = []
            for pl in payloads:
                if isinstance(pl, dict):
                    t = pl.get("text")
                    if isinstance(t, str) and t:
                        pieces.append(t)
            if pieces:
                text = "\n".join(pieces)

        # Forward-compat shapes (newer envelopes)
        if not text and isinstance(d.get("result"), str) and d["result"]:
            text = d["result"]
        data = d.get("data") if isinstance(d.get("data"), dict) else {}
        if not text:
            for key in ("result", "message", "text", "reply"):
                v = data.get(key) or d.get(key)
                if isinstance(v, str) and v:
                    text = v
                    break
        am = data.get("assistantMessage") or d.get("assistantMessage") or {}
        if not text and isinstance(am, dict):
            content = am.get("content")
            if isinstance(content, str) and content:
                text = content
            elif isinstance(content, list):
                pieces = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        t = blk.get("text", "")
                        if isinstance(t, str):
                            pieces.append(t)
                if pieces:
                    text = "\n".join(pieces)

        # Tool calls — locations vary across versions; check several
        meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
        agent_meta = meta.get("agentMeta") if isinstance(meta.get("agentMeta"), dict) else {}
        tcs = d.get("toolCalls") or data.get("toolCalls") or agent_meta.get("toolCalls") or []
        if isinstance(tcs, list):
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                name = tc.get("name") or tc.get("tool") or ""
                if name:
                    tools_used.append(str(name))
                out = tc.get("result") or tc.get("output")
                if out:
                    tool_outputs.append(str(out)[:2000])

    return text, tools_used, tool_outputs


async def _run_openclaw(message: str, profile: str, agent_id: str, model: str) -> tuple[str, str]:
    binary = _resolve_bin()
    args = [
        binary,
        "--profile",
        profile,
        "agent",
        "--local",
        "--json",
        "--agent",
        agent_id,
        "--model",
        model,
        "--message",
        message,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(),
            timeout=DEFAULT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


async def chat(registry: AppRegistry, app: AgentApp, message: str, session_id: str) -> dict:
    get_or_create_agent(registry, app)

    profile = _resolve_profile(registry, app)
    agent_id = _resolve_agent_id(app)
    model = _resolve_model(app)
    rendered = _build_message(registry, app, message, session_id)

    registry._add_to_history(session_id, "user", message)
    text = ""
    tools_used: list[str] = []
    tool_outputs: list[str] = []
    try:
        stdout, stderr = await _run_openclaw(rendered, profile, agent_id, model)
        text, tools_used, tool_outputs = _parse_openclaw_json(stdout)
        if not text:
            log.warning("[openclaw] empty result; stderr=%s", stderr.strip()[:500])
            text = f"[openclaw] runtime returned no reply. stderr: {stderr.strip()[:500]}"
    except asyncio.TimeoutError:
        text = f"[openclaw] timed out after {DEFAULT_TIMEOUT_S:.0f}s"
        log.warning("[openclaw] timeout for app=%s session=%s", app.id, session_id)
    except FileNotFoundError as e:
        text = f"[openclaw] binary not found: {e}"
        log.error("[openclaw] binary missing — set OPENCLAW_BIN or install openclaw CLI")

    registry._add_to_history(session_id, "assistant", text)
    return {"text": text, "tools_used": tools_used, "tool_outputs": tool_outputs}


async def stream(
    registry: AppRegistry, app: AgentApp, message: str, session_id: str
) -> AsyncIterator[dict]:
    result = await chat(registry, app, message, session_id)
    yield {
        "type": "response",
        "text": result["text"],
        "tools_used": result.get("tools_used", []),
        "tool_outputs": result.get("tool_outputs", []),
    }
