"""Pi CLI runtime adapter (standalone — not pi-via-openclaw)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..app_registry import AgentApp, AppRegistry

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = float(os.environ.get("RUNSPACE_CLI_TIMEOUT", "120"))
PI_BIN_ENV = "PI_BIN"
PI_BIN_DEFAULT = "pi"
DEFAULT_PROVIDER = "router"
DEFAULT_MODEL = "gpt-5.4-codex"


def get_or_create_agent(registry: AppRegistry, app: AgentApp):  # noqa: ARG001
    if app._agent is None:
        app._agent = {"runtime": "pi"}
    return app._agent


def build_gate_manager(registry: AppRegistry, app: AgentApp):  # noqa: ARG001
    return None


def _resolve_cwd(app: AgentApp) -> str:
    if app.workspace_path:
        return app.workspace_path
    if app.soul_path:
        guess = str(Path(app.soul_path).parent.parent)
        log.warning("[pi] app=%s no workspace_path; falling back to %s", app.id, guess)
        return guess
    return os.getcwd()


def _resolve_bin() -> str:
    return os.environ.get(PI_BIN_ENV) or shutil.which(PI_BIN_DEFAULT) or PI_BIN_DEFAULT


def _resolve_provider_model(app: AgentApp) -> tuple[str, str]:
    """Parse `app.model` into (provider, model). Accepts 'prov/model' or 'model'."""
    if app.model and "/" in app.model:
        provider, _, model = app.model.partition("/")
        return provider, model
    return DEFAULT_PROVIDER, app.model or DEFAULT_MODEL


def _resolve_allowed_tools(app: AgentApp) -> list[str] | None:
    cfg = app.gates_config or {}
    tools = cfg.get("cli_allowed_tools") or cfg.get("pi_allowed_tools")
    if isinstance(tools, list) and tools:
        return [str(t) for t in tools]
    if isinstance(tools, str) and tools:
        return [t.strip() for t in tools.split(",") if t.strip()]
    return None


def _build_message(registry: AppRegistry, app: AgentApp, message: str, session_id: str) -> str:
    """Render history + envelope into pi's user-message text.

    The persona prompt goes via --system-prompt (separate flag); we still
    prepend prior history into the user message because pi's --no-session
    mode is per-call ephemeral.
    """
    from runspace.protocols.prompt import build_message_envelope

    parts: list[str] = []
    history = registry._get_history(session_id)
    if history:
        parts.append("## Conversation so far")
        for turn in history:
            role = turn.get("role", "user").upper()
            content = (turn.get("content") or "").strip()
            if content:
                parts.append(f"[{role}]\n{content}")
        parts.append("## Current request")

    envelope = build_message_envelope(
        message,
        company=registry.workspace_name.replace(" Back Office", "") or None,
        user_name=registry._user_name or None,
        user_role=registry._user_role or None,
    )
    parts.append(envelope)
    return "\n\n".join(parts)


def _parse_pi_jsonl(stdout: str) -> tuple[str, list[str], list[str]]:
    """Walk `pi --print --mode json` JSONL events.

    Real shape (verified live with 0.74.0):
      - {"type":"session", ...}
      - {"type":"agent_start"} / {"type":"turn_start"}
      - {"type":"message_start","message":{"role":"user|assistant",content:[...]}}
      - {"type":"message_update","assistantMessageEvent":{type:"text_*"|"tool_*",...}}
      - {"type":"message_end","message":{...}}
      - {"type":"turn_end","message":{...},"toolResults":[...]}
      - {"type":"agent_end","messages":[...]}

    Final reply = last assistant message. Tools = tool_use blocks.
    """
    final_text = ""
    tools_used: list[str] = []
    tool_outputs: list[str] = []
    last_assistant_msg: dict | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue

        ev_type = ev.get("type", "")

        # Track latest assistant message envelope
        msg = ev.get("message")
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            last_assistant_msg = msg

        # agent_end carries the full message list — pull the final assistant text
        if ev_type == "agent_end":
            messages = ev.get("messages") or []
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "assistant":
                    last_assistant_msg = m
                    break

        # Real pi 0.74 shapes (verified live):
        #   {"type":"tool_execution_start","toolCallId":"…","toolName":"bash","args":{...}}
        #   {"type":"tool_execution_end","toolCallId":"…","result":"…"}
        if ev_type == "tool_execution_start":
            name = ev.get("toolName") or ev.get("name") or ""
            if name:
                tools_used.append(str(name))
            args = ev.get("args")
            if isinstance(args, dict):
                cmd = args.get("command") or args.get("path")
                if cmd:
                    tool_outputs.append(f"$ {str(cmd)[:500]}")
        elif ev_type == "tool_execution_end":
            out = ev.get("result") or ev.get("output")
            if out:
                tool_outputs.append(str(out)[:2000])

    if last_assistant_msg:
        content = last_assistant_msg.get("content")
        if isinstance(content, str):
            final_text = content
        elif isinstance(content, list):
            pieces: list[str] = []
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    t = blk.get("text", "")
                    if isinstance(t, str):
                        pieces.append(t)
                elif isinstance(blk, dict) and blk.get("type") == "tool_use":
                    name = blk.get("name", "")
                    if name and name not in tools_used:
                        tools_used.append(str(name))
            if pieces:
                final_text = "\n".join(pieces)

    return final_text, tools_used, tool_outputs


async def _run_pi(
    message: str,
    cwd: str,
    provider: str,
    model: str,
    system_prompt: str,
    allowed_tools: list[str] | None,
) -> tuple[str, str]:
    binary = _resolve_bin()
    args = [
        binary,
        "--print",
        "--mode",
        "json",
        "--provider",
        provider,
        "--model",
        model,
        "--no-session",
        "--no-context-files",  # don't pull AGENTS.md/CLAUDE.md from cwd
    ]
    if system_prompt:
        args += ["--append-system-prompt", system_prompt]
    if allowed_tools:
        args += ["--tools", ",".join(allowed_tools)]
    args.append(message)

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=DEFAULT_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


async def chat(registry: AppRegistry, app: AgentApp, message: str, session_id: str) -> dict:
    get_or_create_agent(registry, app)

    cwd = _resolve_cwd(app)
    provider, model = _resolve_provider_model(app)
    allowed_tools = _resolve_allowed_tools(app)
    system_prompt = (app._soul_text or "").strip()
    rendered = _build_message(registry, app, message, session_id)

    registry._add_to_history(session_id, "user", message)
    text = ""
    tools_used: list[str] = []
    tool_outputs: list[str] = []
    try:
        stdout, stderr = await _run_pi(
            rendered,
            cwd,
            provider,
            model,
            system_prompt,
            allowed_tools,
        )
        text, tools_used, tool_outputs = _parse_pi_jsonl(stdout)
        if not text:
            log.warning("[pi] empty assistant text; stderr=%s", stderr.strip()[:500])
            text = f"[pi] runtime returned no reply. stderr: {stderr.strip()[:500]}"
    except asyncio.TimeoutError:
        text = f"[pi] timed out after {DEFAULT_TIMEOUT_S:.0f}s"
        log.warning("[pi] timeout for app=%s session=%s", app.id, session_id)
    except FileNotFoundError as e:
        text = f"[pi] binary not found: {e}"
        log.error("[pi] binary missing — set PI_BIN or install `@earendil-works/pi-coding-agent`")

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
