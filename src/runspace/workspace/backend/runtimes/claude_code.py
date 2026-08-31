"""Claude Code CLI runtime adapter."""

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
CLAUDE_BIN_ENV = "CLAUDE_CODE_BIN"
CLAUDE_BIN_DEFAULT = "claude"
DEFAULT_PERMISSION_MODE = "plan"


def get_or_create_agent(registry: AppRegistry, app: AgentApp):  # noqa: ARG001
    if app._agent is None:
        app._agent = {"runtime": "claude_code"}
    return app._agent


def build_gate_manager(registry: AppRegistry, app: AgentApp):  # noqa: ARG001
    return None


def _resolve_cwd(app: AgentApp) -> str:
    if app.workspace_path:
        return app.workspace_path
    if app.soul_path:
        guess = str(Path(app.soul_path).parent.parent)
        log.warning("[claude_code] app=%s has no workspace_path; falling back to %s", app.id, guess)
        return guess
    return os.getcwd()


def _resolve_bin() -> str:
    return os.environ.get(CLAUDE_BIN_ENV) or shutil.which(CLAUDE_BIN_DEFAULT) or CLAUDE_BIN_DEFAULT


def _resolve_permission_mode(app: AgentApp) -> str:
    cfg = app.gates_config or {}
    mode = cfg.get("cli_permission_mode")
    if mode in ("plan", "acceptEdits", "bypassPermissions", "default"):
        return mode
    return DEFAULT_PERMISSION_MODE


def _resolve_allowed_tools(app: AgentApp) -> list[str] | None:
    cfg = app.gates_config or {}
    tools = cfg.get("cli_allowed_tools")
    if isinstance(tools, list) and tools:
        return [str(t) for t in tools]
    if isinstance(tools, str) and tools:
        return [t.strip() for t in tools.split(",") if t.strip()]
    return None


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


def _parse_claude_stream_json(stdout: str) -> tuple[str, list[str], list[str], dict]:
    """Walk `claude -p --output-format stream-json` JSONL.

    Returns (final_text, tools_used, tool_outputs, meta).

    Event shapes (from Claude Code SDK docs):
      - {"type": "system", "subtype": "init", ...}
      - {"type": "assistant", "message": {"content": [{"type":"text","text":...},
                                                       {"type":"tool_use","name":...}]}}
      - {"type": "user", "message": {"content": [{"type":"tool_result", ...}]}}
      - {"type": "result", "result": "...", "total_cost_usd": ..., "session_id": ...}
    """
    final_text = ""
    tools_used: list[str] = []
    tool_outputs: list[str] = []
    meta: dict = {}

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

        if ev_type == "result":
            res = ev.get("result")
            if isinstance(res, str) and res:
                final_text = res
            if "total_cost_usd" in ev:
                meta["cost_usd"] = ev["total_cost_usd"]
            if "session_id" in ev:
                meta["session_id"] = ev["session_id"]
            continue

        msg = ev.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                name = block.get("name", "")
                if name:
                    tools_used.append(str(name))
            elif btype == "tool_result":
                out = block.get("content")
                if isinstance(out, list):
                    out = " ".join(b.get("text", "") for b in out if isinstance(b, dict))
                if out:
                    tool_outputs.append(str(out)[:2000])
            elif btype == "text" and not final_text:
                # Fallback: latest assistant text — used only if no `result` event arrives.
                txt = block.get("text", "")
                if isinstance(txt, str) and txt:
                    final_text = txt
    return final_text, tools_used, tool_outputs, meta


async def _run_claude(
    prompt: str,
    cwd: str,
    model: str | None,
    permission_mode: str,
    allowed_tools: list[str] | None = None,
) -> tuple[str, str]:
    binary = _resolve_bin()
    args = [
        binary,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",  # required by claude -p with stream-json
        "--add-dir",
        cwd,
        "--permission-mode",
        permission_mode,
    ]
    if allowed_tools:
        # Claude Code accepts comma-separated patterns like "Read,Bash(python:*)"
        args += ["--allowedTools", ",".join(allowed_tools)]
    if model:
        args += ["--model", model]

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
    permission_mode = _resolve_permission_mode(app)
    allowed_tools = _resolve_allowed_tools(app)
    prompt = _build_prompt(registry, app, message, session_id)

    registry._add_to_history(session_id, "user", message)
    text = ""
    tools_used: list[str] = []
    tool_outputs: list[str] = []
    try:
        stdout, stderr = await _run_claude(prompt, cwd, app.model, permission_mode, allowed_tools)
        text, tools_used, tool_outputs, meta = _parse_claude_stream_json(stdout)
        if not text:
            log.warning("[claude_code] no result/text event; stderr=%s", stderr.strip()[:500])
            text = f"[claude_code] runtime returned no reply. stderr: {stderr.strip()[:500]}"
        elif meta.get("cost_usd") is not None:
            log.info(
                "[claude_code] app=%s cost=$%.4f tools=%d",
                app.id,
                meta["cost_usd"],
                len(tools_used),
            )
    except asyncio.TimeoutError:
        text = f"[claude_code] timed out after {DEFAULT_TIMEOUT_S:.0f}s"
        log.warning("[claude_code] timeout for app=%s session=%s", app.id, session_id)
    except FileNotFoundError as e:
        text = f"[claude_code] binary not found: {e}"
        log.error("[claude_code] binary missing — set CLAUDE_CODE_BIN or install `claude` CLI")

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
