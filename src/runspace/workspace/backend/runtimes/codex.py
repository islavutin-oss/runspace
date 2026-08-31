"""Codex CLI runtime adapter."""

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
CODEX_BIN_ENV = "CODEX_BIN"
CODEX_BIN_DEFAULT = "codex"


def get_or_create_agent(registry: AppRegistry, app: AgentApp):  # noqa: ARG001
    """No agentino.Agent for CLI runtimes — return a sentinel marker."""
    if app._agent is None:
        app._agent = {"runtime": "codex"}
    return app._agent


def build_gate_manager(registry: AppRegistry, app: AgentApp):  # noqa: ARG001
    """Gates are an agentino concept; CLI runtimes have none."""
    return None


def _resolve_cwd(app: AgentApp) -> str:
    if app.workspace_path:
        return app.workspace_path
    if app.soul_path:
        guess = str(Path(app.soul_path).parent.parent)
        log.warning("[codex] app=%s has no workspace_path; falling back to %s", app.id, guess)
        return guess
    return os.getcwd()


def _resolve_bin() -> str:
    return os.environ.get(CODEX_BIN_ENV) or shutil.which(CODEX_BIN_DEFAULT) or CODEX_BIN_DEFAULT


def _build_prompt(registry: AppRegistry, app: AgentApp, message: str, session_id: str) -> str:
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


def _parse_codex_jsonl(stdout: str) -> tuple[str, list[str], list[str]]:
    """Walk `codex exec --json` events; return (final_text, tools_used, tool_outputs).

    Real CLI 0.117 event shapes (verified live):
      - {"type":"thread.started","thread_id":...}
      - {"type":"turn.started"}
      - {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
      - {"type":"item.completed","item":{"type":"command_execution",
            "command":"...","aggregated_output":"...","exit_code":0}}
      - {"type":"turn.completed","usage":{...}}

    The final assistant answer is the LAST `agent_message`. Tool calls
    are surfaced as `command_execution` items (codex's only tool surface
    is bash). Older or pre-shaped events with a flat `type=agent_message`
    are also accepted for forward-compat.
    """
    final_text = ""
    tools_used: list[str] = []
    tool_outputs: list[str] = []
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

        # Real shape: nested under item
        if ev.get("type") == "item.completed":
            item = ev.get("item") or {}
            if not isinstance(item, dict):
                continue
            itype = item.get("type", "")
            if itype == "agent_message":
                txt = item.get("text", "")
                if isinstance(txt, str) and txt:
                    final_text = txt
            elif itype == "command_execution":
                cmd = item.get("command", "")
                if cmd:
                    tools_used.append("bash")
                out = item.get("aggregated_output", "")
                if out:
                    tool_outputs.append(str(out)[:2000])
            elif itype in ("function_call", "tool_call"):
                # Forward-compat for future codex versions exposing MCP tools
                name = item.get("name") or item.get("tool") or ""
                if name:
                    tools_used.append(str(name))
            continue

        # Forward-compat / older shape: flat top-level events
        ev_type = ev.get("type", "")
        if ev_type == "agent_message":
            txt = ev.get("text") or ev.get("message") or ev.get("content") or ""
            if isinstance(txt, str) and txt:
                final_text = txt
        elif ev_type in ("tool_call", "function_call"):
            name = ev.get("name") or ev.get("tool") or ""
            if name:
                tools_used.append(str(name))
        elif ev_type in ("tool_output", "tool_result"):
            out = ev.get("output") or ev.get("result") or ""
            if out:
                tool_outputs.append(str(out)[:2000])
    return final_text, tools_used, tool_outputs


async def _run_codex(prompt: str, cwd: str, model: str | None) -> tuple[str, str]:
    binary = _resolve_bin()
    args = [binary, "exec", "--json", "--skip-git-repo-check"]
    if model:
        args += ["--model", model]
    args.append("-")  # read prompt from stdin

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")),
            timeout=DEFAULT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


async def chat(registry: AppRegistry, app: AgentApp, message: str, session_id: str) -> dict:
    get_or_create_agent(registry, app)

    cwd = _resolve_cwd(app)
    prompt = _build_prompt(registry, app, message, session_id)

    registry._add_to_history(session_id, "user", message)
    text = ""
    tools_used: list[str] = []
    tool_outputs: list[str] = []
    try:
        stdout, stderr = await _run_codex(prompt, cwd, app.model)
        text, tools_used, tool_outputs = _parse_codex_jsonl(stdout)
        if not text:
            log.warning("[codex] no agent_message in JSONL; stderr=%s", stderr.strip()[:500])
            text = f"[codex] runtime returned no agent_message. stderr: {stderr.strip()[:500]}"
    except asyncio.TimeoutError:
        text = f"[codex] timed out after {DEFAULT_TIMEOUT_S:.0f}s"
        log.warning("[codex] timeout for app=%s session=%s", app.id, session_id)
    except FileNotFoundError as e:
        text = f"[codex] binary not found: {e}"
        log.error("[codex] binary missing — set CODEX_BIN or install `codex` CLI")

    registry._add_to_history(session_id, "assistant", text)
    return {"text": text, "tools_used": tools_used, "tool_outputs": tool_outputs}


async def stream(
    registry: AppRegistry, app: AgentApp, message: str, session_id: str
) -> AsyncIterator[dict]:
    """Subprocess runtime — yield a single final response delta."""
    result = await chat(registry, app, message, session_id)
    yield {
        "type": "response",
        "text": result["text"],
        "tools_used": result.get("tools_used", []),
        "tool_outputs": result.get("tool_outputs", []),
    }
