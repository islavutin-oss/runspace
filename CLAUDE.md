# CLAUDE.md — Runspace

Notes for coding agents (and humans) working in this repository.

## What this is

A workspace runtime for agents: a FastAPI gateway, an app registry, chat with
streaming and attachments, external channels, scheduled routines, and a React
frontend. It is runtime-agnostic by construction — the agentino adapter is one
runtime among several, and `app_registry.py` contains no `from agentino`
imports.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[workspace]"
pip install pytest pytest-asyncio hypothesis ruff
```

Agentino is an optional extra, not a dependency — `pip install -e ".[agentino]"`
if you want the in-process runtime. The test suite exercises it, so install it
for development.

## Commands

```bash
PYTHONPATH=src pytest -q                                            # default paths
PYTHONPATH=src pytest src/runspace/contracts src/runspace/protocols \
    src/runspace/workspace/cli/tests scripts/tests -q
node --experimental-strip-types workspace/frontend/shared/utils/loosePayload.test.mjs

ruff check .            # must be clean
ruff format --check .   # must be clean
PYTHONPATH=src python -m runspace.protocols.sandbox_lint <dir>   # flag banned imports in agent tools
```

CI runs all of these.

## Layout

```
runspace/
├── src/runspace/            one import root — everything ships as runspace.*
│   ├── contracts/           wire shapes — chat, runtime, tool, workspace.yml
│   ├── protocols/           swappable adapters behind Protocols:
│   │                        store, vision, transport, file_storage, embeddings,
│   │                        transcriber, clock, prompt flattening
│   ├── workspace/backend/   gateway (FastAPI routes), app_registry, runtimes/,
│   │                        messaging, routines, attachments, runners, scoring
│   ├── ingestion/           inbound channels — Telegram polling, pairing
│   ├── helpers/             session, messaging and document utilities
│   └── runspace_cli/        the `runspace` console entry point
├── workspace/frontend/      React components published as @runspace/ui — an npm
│                            package, deliberately outside the Python tree
└── templates/               workspace.yml.example
```

## Conventions that matter here

- **No application logic.** This is a shared layer. Tenant behaviour comes
  from configuration — `workspace.yml`, `routines.yml`, `SOUL.md` — never from
  a branch in Python. That includes prompt wording, retry thresholds, tool
  names and natural language. Where an application needs to influence
  behaviour, give it a seam: `response_filter` names a callable the app owns,
  and runspace calls it without knowing what it checks.
- **No hardcoded prompts.** Agent instructions live in `SOUL.md`. Frontend
  rendering rules live in a partial the SOUL includes, not in a Python
  constant.
- **No deployment-specific environment variables.** The public names are
  `AI_BASE_URL` / `AI_API_KEY`, `EMBEDDINGS_*` and `VISION_API_KEY`. Use
  `${VAR:-default}` in YAML; `_resolve_env_vars` expands it at load time.
- **Runtime-agnostic by construction.** Only
  `src/runspace/workspace/backend/runtimes/agentino.py` imports agentino, and it is one of
  five adapters — `codex`, `claude_code`, `pi` and `openclaw` shell out to a
  CLI instead. `app_registry.py` dispatches on `app.type` and imports none of
  them eagerly. A sixth drops in beside them.
- **One source of truth for prompts.** `protocols.prompt.flatten_soul` is what
  every caller uses to produce a flattened SOUL, so they cannot drift.
- **Property tests build their own fixtures.** `@given` runs many examples
  against one function-scoped fixture, so shared mutable state leaks between
  examples and looks like a bug in the code under test. Hand back a factory.
