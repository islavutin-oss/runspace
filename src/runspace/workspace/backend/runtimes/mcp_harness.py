"""MCP-harness launcher — build the subprocess command to run an agent CLI against an MCP server."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class McpServer:
    """A stdio MCP server for a harness to attach: how to spawn it + its env."""

    name: str
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)


def _which(env_var: str, default: str) -> str:
    return os.environ.get(env_var) or shutil.which(default) or default


# ---------------------------------------------------------------------------
# Per-harness command builders
# ---------------------------------------------------------------------------


def _agentino_cmd(*, agents_yml, agent_name, message, python_bin) -> list[str]:
    # agentino is in-process; the MCP server is attached via agents.yml's
    # tools_dir adapter. --no-session keeps every run ephemeral; -u so a
    # killed run still leaves a flushed, diagnosable trace.
    return [
        python_bin or sys.executable,
        "-u",
        "-m",
        "agentino",
        "run",
        str(agents_yml),
        "-a",
        agent_name,
        "-m",
        message,
        "--mode",
        "jsonl",
        "--quiet",
        "--no-session",
    ]


def _pi_cmd(*, message, soul_text, model, pi_extension, provider) -> list[str]:
    return [
        _which("PI_BIN", "pi"),
        "--print",
        "--mode",
        "json",
        "--provider",
        provider,
        "--model",
        model,
        "--no-session",
        "--no-context-files",
        "--no-builtin-tools",
        "--extension",
        str(pi_extension),
        "--append-system-prompt",
        soul_text,
        message,
    ]


def ensure_codex_mcp_server(mcp: McpServer) -> None:
    """Register `mcp` in ~/.codex/config.toml if it isn't already.

    codex treats a fully `-c`-injected MCP server as untrusted and cancels
    every tool call against it ("user cancelled MCP tool call"). A server
    defined in config.toml is trusted. command + args are static so they live
    in the file; per-run env is overridden via `-c` at launch.
    """
    cfg = Path.home() / ".codex" / "config.toml"
    header = f"[mcp_servers.{mcp.name}]"
    if cfg.exists() and header in cfg.read_text():
        return
    args_toml = ", ".join(json.dumps(a) for a in mcp.args)
    block = f"\n{header}\ncommand = {json.dumps(mcp.command)}\nargs = [{args_toml}]\n"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    with cfg.open("a") as f:
        f.write(block)


def _codex_cmd(*, message, soul_text, model, mcp, reasoning) -> list[str]:
    ensure_codex_mcp_server(mcp)
    prompt = (soul_text + "\n\n---\n\n" + message) if soul_text else message
    cmd = [
        _which("CODEX_BIN", "codex"),
        "exec",
        "--json",
        # codex 0.117 headless `exec` cancels EVERY MCP tool call client-side
        # ("user cancelled MCP tool call") under any approval_policy, whether
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--ephemeral",
        "-m",
        model,
        "-c",
        f"model_reasoning_effort={reasoning}",
    ]
    for key, val in mcp.env.items():
        cmd += ["-c", f'mcp_servers.{mcp.name}.env.{key}="{val}"']
    cmd.append(prompt)
    return cmd


def _claude_cmd(*, message, soul_text, model, mcp) -> list[str]:
    mcp_cfg = json.dumps(
        {"mcpServers": {mcp.name: {"command": mcp.command, "args": mcp.args, "env": mcp.env}}}
    )
    return [
        _which("CLAUDE_BIN", "claude"),
        "-p",
        message,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--mcp-config",
        mcp_cfg,
        "--allowedTools",
        f"mcp__{mcp.name}",
        "--append-system-prompt",
        soul_text,
        "--model",
        model,
    ]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

HARNESSES = ("agentino", "pi", "codex", "claude")


def build_command(
    harness: str,
    *,
    message: str,
    mcp: McpServer | None = None,
    soul_text: str = "",
    model: str = "",
    agents_yml: str | Path | None = None,
    agent_name: str | None = None,
    pi_extension: str | Path | None = None,
    pi_provider: str = "router",
    claude_model: str = "sonnet",
    codex_reasoning: str = "high",
    python_bin: str | None = None,
) -> list[str]:
    """Build the argv to run `harness` against an MCP server.

    `mcp` is required for codex / claude (they attach it via the command).
    agentino attaches it via agents.yml; pi via its extension.
    """
    if harness == "agentino":
        if not (agents_yml and agent_name):
            raise ValueError("agentino harness needs agents_yml + agent_name")
        return _agentino_cmd(
            agents_yml=agents_yml, agent_name=agent_name, message=message, python_bin=python_bin
        )
    if harness == "pi":
        if not pi_extension:
            raise ValueError("pi harness needs pi_extension")
        return _pi_cmd(
            message=message,
            soul_text=soul_text,
            model=model,
            pi_extension=pi_extension,
            provider=pi_provider,
        )
    if harness == "codex":
        if mcp is None:
            raise ValueError("codex harness needs an McpServer")
        return _codex_cmd(
            message=message, soul_text=soul_text, model=model, mcp=mcp, reasoning=codex_reasoning
        )
    if harness == "claude":
        if mcp is None:
            raise ValueError("claude harness needs an McpServer")
        return _claude_cmd(message=message, soul_text=soul_text, model=claude_model, mcp=mcp)
    raise ValueError(f"unknown harness: {harness!r} (known: {HARNESSES})")
