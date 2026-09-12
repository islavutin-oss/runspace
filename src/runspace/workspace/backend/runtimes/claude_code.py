"""Claude Code CLI runtime adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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


_DISPATCHER_CALL = re.compile(r"(?:^|[\s;&|(])(?:\./)?tools\s+call\s+([A-Za-z0-9_.:-]+)")

# The CLI's own refusal when a command falls outside `--allowedTools`. It comes
# back as a `tool_result` with `is_error`, so it is indistinguishable from a
# tool that ran and failed unless the text is read: one is a call the agent
# made, the other is a call that never happened.
_PERMISSION_REFUSED = re.compile(
    r"requires approval|requested permissions|have(?:n't| not) granted", re.I
)


def _tool_display_name(block: dict, own_tools_only: bool = False) -> str:
    """The name to show for a `tool_use` block.

    Claude Code reports its own tools — `Bash`, `Read` — but an agent that
    reaches its real tools through a shell dispatcher (`./tools call <name>`)
    is calling `<name>`; that is what the reader should see, and what
    `tools_used` should record.

    With `own_tools_only` anything else is dropped (returns ""): once a
    workspace has a dispatcher, the CLI's own tools are plumbing — the `ls`
    before the call, the `Read` of a file the tool wrote — and a reader
    shown "Bash, Bash, Bash, lookup_order, Bash" learns nothing from the
    Bashes. Without a dispatcher the CLI's tools are all the agent has, so
    they keep their names.
    """
    name = str(block.get("name") or "")
    if name == "Bash":
        inp = block.get("input")
        command = inp.get("command") if isinstance(inp, dict) else None
        if isinstance(command, str):
            m = _DISPATCHER_CALL.search(command)
            if m:
                return m.group(1)
    return "" if own_tools_only else name


def _has_dispatcher(cwd: str) -> bool:
    """Does this workspace reach its tools through `./tools`?"""
    return (Path(cwd) / "tools").is_file()


class _StreamState:
    """Fold `claude -p --output-format stream-json` events as they arrive.

    Event shapes (from Claude Code SDK docs):
      - {"type": "system", "subtype": "init", ...}
      - {"type": "assistant", "message": {"content": [{"type":"text","text":...},
                                                       {"type":"tool_use","name":...}]}}
      - {"type": "user", "message": {"content": [{"type":"tool_result", ...}]}}
      - {"type": "result", "result": "...", "total_cost_usd": ..., "session_id": ...}

    `feed` returns the tool names a single event announced, so a streaming
    caller can tell the reader what is running while the turn is still going
    — the CLI emits each `tool_use` the moment it happens, and a turn that
    calls tools for minutes is silent otherwise.
    """

    def __init__(self, own_tools_only: bool = False) -> None:
        self.own_tools_only = own_tools_only
        self.final_text = ""
        self.tools_used: list[str] = []
        self.tool_outputs: list[str] = []
        self.meta: dict = {}
        # tool_use id -> reported name, until the matching result arrives.
        self._pending: dict[str, str] = {}
        self.refused: list[str] = []

    def feed(self, ev: dict) -> list[str]:
        ev_type = ev.get("type", "")
        if ev_type == "result":
            res = ev.get("result")
            if isinstance(res, str) and res:
                self.final_text = res
            if "total_cost_usd" in ev:
                self.meta["cost_usd"] = ev["total_cost_usd"]
            if "session_id" in ev:
                self.meta["session_id"] = ev["session_id"]
            return []

        msg = ev.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            return []
        announced: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                name = _tool_display_name(block, self.own_tools_only)
                if name:
                    self.tools_used.append(name)
                    announced.append(name)
                    call_id = block.get("id")
                    if isinstance(call_id, str):
                        self._pending[call_id] = name
            elif btype == "tool_result":
                out = block.get("content")
                if isinstance(out, list):
                    out = " ".join(b.get("text", "") for b in out if isinstance(b, dict))
                out = str(out) if out else ""
                name = self._pending.pop(block.get("tool_use_id"), None)
                if block.get("is_error") and _PERMISSION_REFUSED.search(out):
                    # The gate refused it: the agent reached for a tool it is
                    # not allowed and nothing ran. Reporting it as used tells
                    # the reader the answer rests on a call that never
                    # happened, and hands the refusal text to anything reading
                    # tool output as evidence.
                    if name and name in self.tools_used:
                        self.tools_used.remove(name)
                        self.refused.append(name)
                    continue
                if out:
                    self.tool_outputs.append(out[:2000])
            elif btype == "text" and not self.final_text:
                # Fallback: latest assistant text — used only if no `result` event arrives.
                txt = block.get("text", "")
                if isinstance(txt, str) and txt:
                    self.final_text = txt
        return announced


def _parse_claude_stream_json(stdout: str) -> tuple[str, list[str], list[str], dict]:
    """Walk a complete stream-json transcript. Returns
    (final_text, tools_used, tool_outputs, meta)."""
    state = _StreamState()
    for ev in _events_from_lines(stdout.splitlines()):
        state.feed(ev)
    return state.final_text, state.tools_used, state.tool_outputs, state.meta


def _events_from_lines(lines) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def _claude_args(
    cwd: str, model: str | None, permission_mode: str, allowed_tools: list[str] | None
) -> list[str]:
    args = [
        _resolve_bin(),
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
    return args


class _ClaudeRun:
    """One `claude -p` invocation, read as it writes.

    `events()` yields each stream-json event when its line arrives rather
    than after the process exits, so a caller can relay tool calls while the
    turn is in flight. The whole run is bounded by `DEFAULT_TIMEOUT_S`; on
    expiry the process is killed and `asyncio.TimeoutError` is raised from
    the generator. `stderr` is available once the generator is exhausted.
    """

    def __init__(
        self,
        prompt: str,
        cwd: str,
        model: str | None,
        permission_mode: str,
        allowed_tools: list[str] | None = None,
    ) -> None:
        self.args = _claude_args(cwd, model, permission_mode, allowed_tools)
        self.cwd = cwd
        self.prompt = prompt
        self.stderr = ""

    async def events(self) -> AsyncIterator[dict]:
        proc = await asyncio.create_subprocess_exec(
            *self.args,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # stdin and stderr are pumped in the background: a prompt larger than
        # the pipe would block a plain write until the child read it, and a
        # child that fills stderr would block on us reading stdout.
        feed = asyncio.create_task(self._feed_stdin(proc))
        drain = asyncio.create_task(proc.stderr.read())
        deadline = asyncio.get_running_loop().time() + DEFAULT_TIMEOUT_S
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                if not line:
                    break
                for ev in _events_from_lines([line.decode("utf-8", errors="replace")]):
                    yield ev
            remaining = max(deadline - asyncio.get_running_loop().time(), 0.0)
            await asyncio.wait_for(proc.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        finally:
            # Settle the pumps rather than cancel them: with the child gone
            # both finish at once, and a feed that never got scheduled
            # (short stub output) must still close stdin.
            try:
                await asyncio.wait_for(feed, timeout=5)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                feed.cancel()
            try:
                err = await drain
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — the pipe is gone with the process
                err = b""
            self.stderr = err.decode("utf-8", errors="replace")

    async def _feed_stdin(self, proc) -> None:
        try:
            proc.stdin.write(self.prompt.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()


async def _run_claude(
    prompt: str,
    cwd: str,
    model: str | None,
    permission_mode: str,
    allowed_tools: list[str] | None = None,
) -> tuple[str, str]:
    """Run to completion; returns (stdout, stderr). Kept for callers that
    want the raw transcript rather than events."""
    run = _ClaudeRun(prompt, cwd, model, permission_mode, allowed_tools)
    lines = [json.dumps(ev) async for ev in run.events()]
    return "\n".join(lines) + ("\n" if lines else ""), run.stderr


async def _turn(
    registry: AppRegistry, app: AgentApp, message: str, session_id: str
) -> AsyncIterator[dict]:
    """The shared turn: announce tool calls as they happen, then the reply.

    Yields `{"type": "tool_call", "name": ...}` per tool the CLI starts and
    one final `{"type": "response", ...}`. History is written here so the
    streaming and non-streaming entry points cannot drift.
    """
    get_or_create_agent(registry, app)

    cwd = _resolve_cwd(app)
    permission_mode = _resolve_permission_mode(app)
    allowed_tools = _resolve_allowed_tools(app)
    prompt = _build_prompt(registry, app, message, session_id)

    registry._add_to_history(session_id, "user", message)
    state = _StreamState(own_tools_only=_has_dispatcher(cwd))
    text = ""
    try:
        run = _ClaudeRun(prompt, cwd, app.model, permission_mode, allowed_tools)
        async for ev in run.events():
            for name in state.feed(ev):
                yield {"type": "tool_call", "name": name}
        text = state.final_text
        if not text:
            log.warning("[claude_code] no result/text event; stderr=%s", run.stderr.strip()[:500])
            text = f"[claude_code] runtime returned no reply. stderr: {run.stderr.strip()[:500]}"
        elif state.meta.get("cost_usd") is not None:
            log.info(
                "[claude_code] app=%s cost=$%.4f tools=%d",
                app.id,
                state.meta["cost_usd"],
                len(state.tools_used),
            )
    except asyncio.TimeoutError:
        text = f"[claude_code] timed out after {DEFAULT_TIMEOUT_S:.0f}s"
        log.warning("[claude_code] timeout for app=%s session=%s", app.id, session_id)
    except FileNotFoundError as e:
        text = f"[claude_code] binary not found: {e}"
        log.error("[claude_code] binary missing — set CLAUDE_CODE_BIN or install `claude` CLI")

    if state.refused:
        # A warning, not an info line: an agent reaching for tools it is not
        # allowed is either a gate too tight for the work or a SOUL promising
        # what the configuration does not grant, and both are misconfiguration
        # an operator should see. Deployments commonly leave their loggers at
        # WARNING, where an info line is never printed at all.
        log.warning(
            "[claude_code] app=%s reached for tools it is not allowed: %s",
            app.id,
            ", ".join(sorted(set(state.refused))),
        )

    registry._add_to_history(session_id, "assistant", text)
    yield {
        "type": "response",
        "text": text,
        "tools_used": state.tools_used,
        "tool_outputs": state.tool_outputs,
    }


async def chat(registry: AppRegistry, app: AgentApp, message: str, session_id: str) -> dict:
    result: dict = {"text": "", "tools_used": [], "tool_outputs": []}
    async for ev in _turn(registry, app, message, session_id):
        if ev["type"] == "response":
            result = {k: ev[k] for k in ("text", "tools_used", "tool_outputs")}
    return result


async def stream(
    registry: AppRegistry, app: AgentApp, message: str, session_id: str
) -> AsyncIterator[dict]:
    async for ev in _turn(registry, app, message, session_id):
        yield ev
