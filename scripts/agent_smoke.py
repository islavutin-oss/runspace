"""Per-agent liveness smoke for runspace tenants.

Iterates apps with `type: agentino` in a tenant's `workspace.yml`, spawns
each agent in its own subprocess via `agentino run --mode json` (the
machine-readable contract added on the agentino `feat/cli-json-output`
branch), sends a small probe, parses the JSON envelope on stdout, and
reports PASS/FAIL. Exits non-zero if any agent fails.

This script is **tenant-agnostic**. It works against any runspace
tenant's workspace.yml (acme, globex, initech, future).
The only tenant-specific bits — provider env-var name (e.g.
`${AI_API_KEY}`) — are read from the workspace.yml
itself and resolved against `os.environ` at runtime, not hardcoded
here.

Subprocess-per-agent because:
  - One agent's auth / SOUL / tool failure doesn't poison the others.
  - Reproducibility — each PASS/FAIL maps to one CLI invocation you can paste.
  - Uses agentino's new `--mode json` contract — no Python interop needed.

What this catches:
  - Silent SOUL drift (the Ada-permission-loop class, even after
    `register()`'s hard-fail — e.g. SOUL exists but flatten resolves
    to empty after include-template chain).
  - Stale provider config / missing env keys (the env var named in
    `providers.<id>.api_key: ${X}` isn't set on the host).
  - Broken `tools:` directory paths after a folder rename.
  - Wrong `model:` for the configured provider.

What this does NOT catch:
  - Domain correctness — this is liveness, not integration testing.
    A bookkeeper agent with subtly wrong totals will pass.
  - openclaw-typed agents — only `type: agentino` is probed today.
    Phase 2 of the hybrid runtime will add an openclaw smoke path.

Usage:
  # All agentino agents in a tenant's workspace.yml (env var resolved
  # from workspace.yml's providers block — set the right one for your tenant)
  AI_API_KEY=$(cat ~/.agentino/router.key) \\
    python3 runspace/scripts/agent_smoke.py /path/to/tenants/acme/workspace.yml

  # One agent only (when reproducing a CI failure locally)
  python3 runspace/scripts/agent_smoke.py <ws.yml> --agent accountant --verbose

  # Comma-separated subset
  python3 runspace/scripts/agent_smoke.py <ws.yml> --agent finance,inventory

Designed to be wired into a host's pre-deploy gate (e.g.
`acme/platform/scripts/pre_deploy_check.sh`).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

# This script lives in runspace/scripts/. Imports use the runspace package
# directly — no $PYTHONPATH wiring needed when invoked as a sibling module.
RUNSPACE_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PROBE = "Reply with the single word OK to confirm you received this."

G = "\033[32m"
R = "\033[31m"
Y = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def _flatten_soul(soul_abs: Path, persona_name: str, tenant_name: str) -> str:
    """Use runspace's `protocols.prompt.flatten_soul`. Returns flattened text."""
    if str(RUNSPACE_ROOT) not in sys.path:
        sys.path.insert(0, str(RUNSPACE_ROOT))
    from runspace.protocols.prompt import flatten_soul  # type: ignore[import-not-found]

    return flatten_soul(soul_abs, persona_name=persona_name, tenant_name=tenant_name)


def _build_temp_agents_yaml(
    workspace_yml: Path,
    app_id: str,
    app_cfg: dict[str, Any],
    providers: dict[str, Any],
    tenant_name: str,
) -> tuple[str, Path]:
    """Synthesise a single-agent agents.yml that agentino's CLI accepts.

    Returns (agent_name, path_to_temp_yml). Caller cleans up — temp file
    lives next to the workspace.yml so agentino's tool discovery + .env
    resolution still finds tenant-relative paths.
    """
    base = workspace_yml.parent
    soul_rel = app_cfg.get("soul")
    if not soul_rel:
        raise ValueError(f"app {app_id!r} has no soul: in workspace.yml")
    soul_abs = (base / soul_rel).resolve()
    if not soul_abs.exists():
        raise FileNotFoundError(f"SOUL.md missing for {app_id!r}: {soul_abs}")

    persona = app_cfg.get("name", app_id.capitalize())
    tenant_label = tenant_name.replace(" Back Office", "") or persona
    soul_text = _flatten_soul(soul_abs, persona_name=persona, tenant_name=tenant_label)

    # Pick the first provider in workspace.yml. Multi-provider tenants pick
    # the right one per-agent via the agent's `model:` prefix; the smoke
    # uses the default provider since the probe message is provider-agnostic.
    provider_id, provider_cfg = next(iter(providers.items()), (None, None))
    if not provider_cfg:
        raise ValueError("no providers: block in workspace.yml")

    tools_rel = app_cfg.get("tools")
    tools_abs: str | None = None
    if tools_rel:
        tools_abs = str((base / tools_rel).resolve())

    # Model must be prefixed with the provider id (`router/gpt-5.3-codex`)
    # so agentino's config layer binds it to that provider's base_url —
    # matches harness's agents.yml convention.
    raw_model = app_cfg.get("model") or "gpt-5.4-codex"
    qualified_model = raw_model if "/" in raw_model else f"{provider_id}/{raw_model}"

    # Workspace.yml's provider block omits the `provider:` kind (it's
    # implicit per-tenant). Agentino's config layer needs it to pick the
    # right transport. Default to openai-codex (the common runspace path).
    if "provider" not in provider_cfg:
        provider_cfg = dict(provider_cfg, provider="openai-codex")

    syn = {
        "providers": {provider_id: provider_cfg},
        "agents": {
            app_id: {
                "model": qualified_model,
                "provider": provider_cfg.get("provider", "openai-codex"),
                "instructions": soul_text,
                **({"tools_dir": tools_abs} if tools_abs else {}),
            }
        },
    }

    fd, tmp_path = tempfile.mkstemp(prefix=f"smoke-{app_id}-", suffix=".yml", dir=str(base))
    os.close(fd)
    Path(tmp_path).write_text(yaml.safe_dump(syn, sort_keys=False))
    return app_id, Path(tmp_path)


def _probe_one(
    workspace_yml: Path,
    app_id: str,
    app_cfg: dict[str, Any],
    providers: dict[str, Any],
    tenant_name: str,
    probe_message: str,
    timeout_s: float,
) -> dict[str, Any]:
    t0 = time.time()
    try:
        _name, temp_yml = _build_temp_agents_yaml(
            workspace_yml,
            app_id,
            app_cfg,
            providers,
            tenant_name,
        )
    except Exception as exc:
        return {
            "agent": app_id,
            "ok": False,
            "elapsed_s": time.time() - t0,
            "reason": f"setup error: {type(exc).__name__}: {exc}"[:200],
            "envelope": None,
            "exit_code": None,
        }

    try:
        args = [
            sys.executable,
            "-m",
            "agentino",
            "run",
            str(temp_yml),
            "--agent",
            app_id,
            "--message",
            probe_message,
            "--mode",
            "json",
            "--quiet",
        ]
        env = os.environ.copy()
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s, env=env)
        except subprocess.TimeoutExpired:
            return {
                "agent": app_id,
                "ok": False,
                "elapsed_s": timeout_s,
                "reason": f"timeout after {timeout_s:.0f}s",
                "envelope": None,
                "exit_code": None,
            }

        elapsed = time.time() - t0
        envelope: dict[str, Any] | None = None
        for line in reversed(proc.stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    envelope = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        if envelope is None:
            stderr_tail = (proc.stderr or "").strip().splitlines()
            reason = stderr_tail[-1] if stderr_tail else f"exit {proc.returncode}"
            return {
                "agent": app_id,
                "ok": False,
                "elapsed_s": elapsed,
                "reason": reason[:200],
                "envelope": None,
                "exit_code": proc.returncode,
            }

        text = (envelope.get("text") or "").strip()
        ok = bool(text) and not text.startswith("[") and "error" not in text.lower()[:40]
        return {
            "agent": app_id,
            "ok": ok,
            "elapsed_s": elapsed,
            "reason": "" if ok else (text[:150] or "empty reply"),
            "envelope": envelope,
            "exit_code": proc.returncode,
        }
    finally:
        try:
            temp_yml.unlink()
        except (OSError, FileNotFoundError):
            pass


def _resolve_providers(providers_raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Resolve `${ENV}` template refs in provider config against os.environ.

    Returns (resolved_providers, list_of_unresolved_env_var_names). Empty
    `unresolved` means every `${X}` template found a value.
    """
    providers: dict[str, Any] = {}
    unresolved: list[str] = []
    for pid, pcfg in providers_raw.items():
        resolved = dict(pcfg)
        for k, v in resolved.items():
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                env_name = v[2:-1]
                env_val = os.environ.get(env_name, "")
                if not env_val:
                    unresolved.append(env_name)
                resolved[k] = env_val
        providers[pid] = resolved
    return providers, unresolved


def main() -> None:
    p = argparse.ArgumentParser(
        description="Per-agent liveness smoke for runspace tenants.",
        epilog="See script docstring for full details.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("workspace_yml", help="Path to a tenant's workspace.yml")
    p.add_argument(
        "--agent",
        "-a",
        help="Comma-separated subset of agent ids to test (default: all agentino agents)",
    )
    p.add_argument(
        "--message", "-m", default=DEFAULT_PROBE, help=f"Probe message (default: {DEFAULT_PROBE!r})"
    )
    p.add_argument(
        "--timeout",
        "-t",
        type=float,
        default=30.0,
        help="Per-agent timeout in seconds (default: 30)",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Print full failure envelopes")
    args = p.parse_args()

    workspace_yml = Path(args.workspace_yml).resolve()
    if not workspace_yml.exists():
        print(f"{R}Error:{RESET} workspace.yml not found: {workspace_yml}", file=sys.stderr)
        sys.exit(2)

    cfg = yaml.safe_load(workspace_yml.read_text(encoding="utf-8")) or {}
    tenant_name = cfg.get("name", workspace_yml.parent.name)
    apps_raw = cfg.get("apps", {})
    providers_raw = cfg.get("providers", {})

    providers, unresolved = _resolve_providers(providers_raw)
    if unresolved:
        # Don't fail outright — let the per-agent probe surface the auth
        # failure with a precise reason. But warn the user what's missing.
        names = ", ".join(sorted(set(unresolved)))
        print(
            f"{Y}warning:{RESET} unresolved env var(s) referenced by providers: {names}",
            file=sys.stderr,
        )

    agentino_apps = {
        app_id: app_cfg
        for app_id, app_cfg in apps_raw.items()
        if app_cfg.get("type", "agentino") == "agentino" and app_cfg.get("enabled", True)
    }
    if args.agent:
        wanted = set(args.agent.split(","))
        agentino_apps = {k: v for k, v in agentino_apps.items() if k in wanted}

    if not agentino_apps:
        print(f"{R}Error:{RESET} no agentino agents found in {workspace_yml}", file=sys.stderr)
        sys.exit(2)

    print(f"{DIM}tenant:{RESET}     {tenant_name}")
    print(f"{DIM}config:{RESET}     {workspace_yml}")
    print(f"{DIM}agents:{RESET}     {len(agentino_apps)} agentino app(s)")
    print(f"{DIM}probe:{RESET}      {args.message!r}")
    print()
    print(f"  {'AGENT':<14} {'STATUS':<6}  {'TIME':>6}  REASON")
    print(f"  {'─' * 14} {'─' * 6}  {'─' * 6}  {'─' * 50}")

    results: list[dict[str, Any]] = []
    for app_id, app_cfg in agentino_apps.items():
        r = _probe_one(
            workspace_yml, app_id, app_cfg, providers, tenant_name, args.message, args.timeout
        )
        results.append(r)
        status = f"{G}PASS{RESET}" if r["ok"] else f"{R}FAIL{RESET}"
        elapsed = f"{r['elapsed_s']:>5.1f}s"
        reason = (r["reason"] or "").replace("\n", " ")[:60]
        print(f"  {app_id:<14} {status:<14}  {elapsed}  {reason}")
        if args.verbose and not r["ok"] and r.get("envelope"):
            print(f"    {DIM}envelope:{RESET} {json.dumps(r['envelope'])[:300]}")

    print()
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    if failed == 0:
        print(f"  {G}{passed}/{len(results)} agents healthy{RESET}")
        sys.exit(0)
    else:
        print(f"  {R}{failed} of {len(results)} failed{RESET}  ({passed} passed)")
        sys.exit(1)


if __name__ == "__main__":
    main()
