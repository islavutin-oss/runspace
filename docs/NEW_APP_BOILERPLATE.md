# New app boilerplate — runspace / WorkspaceGateway / DialogChat

Recipe for spinning up a new tenant-style app (chat + custom dashboards) on
the runspace stack.

The reusable pieces (do **not** re-invent these):

- **Backend** — `workspace.backend.WorkspaceGateway` (FastAPI router) handles
  chat/streaming, agents, files, threads, history. You write *agents* and
  *tools*, not a chat backend.
- **Frontend** — `@agentino/workspace/pages/WorkspaceShell` wraps DialogChat
  (ChatGPT-style) + DashboardPanel (iframe tabs) + ModeSwitcher. You write
  *page glue*, not a chat UI.
- **Kanban** — `@agentino/workspace/components/Kanban` if your app needs a
  drag-and-drop board (globex uses it for supplier-comm cards).

## Repo layout (mirror this)

```
my-app/
├── workspace.yml                 # WorkspaceGateway entrypoint
├── agents/
│   └── <agent-id>/
│       ├── SOUL.md               # system prompt
│       ├── agents.yml            # provider + model (router/codex)
│       └── tools/
│           ├── tool_one.py       # one @tool per file
│           ├── tool_two.py
│           └── ...
├── api/
│   ├── main.py                   # FastAPI mounting WorkspaceGateway + custom routes
│   ├── store.py                  # your domain store (file-based, DB, etc.)
│   └── persona.py                # extra agents that DO NOT need full chat UX
├── web/
│   ├── package.json              # next 15, react 18, lucide, react-markdown, mermaid, recharts
│   ├── next.config.js            # webpack alias @agentino/workspace + rewrites for /api/*
│   ├── tailwind.config.ts        # content globs include ../../runspace/workspace/frontend/**
│   ├── tsconfig.json             # paths alias @agentino/workspace/*
│   └── src/
│       ├── app/page.tsx          # imports WorkspaceShell + your custom views
│       ├── app/layout.tsx
│       ├── app/globals.css
│       └── components/demoSeeds.ts  # idempotent localStorage seeding for demo dialogs
├── data/                         # gitignored — domain state
└── .env                          # AI_API_KEY, AI_BASE_URL, CODEX_MODEL
```

## Backend wiring (`api/main.py`)

```python
from fastapi import FastAPI
from runspace.workspace.backend import WorkspaceGateway

app = FastAPI()
app.include_router(
    WorkspaceGateway.from_config("workspace.yml", base_dir=ROOT).router
)
# + your domain routes (cards, threads, etc.)
```

That's it for chat. Threads, streaming, history, file uploads — all handled.

## Tool authoring (`agents/<agent-id>/tools/*.py`)

One file per tool. Each file exports a single `@tool`-decorated callable:

```python
from agentino import tool
from myapp import store  # your own module, not runspace's

@tool(is_read_only=True)
async def list_things(status: str = "") -> list[dict]:
    """Docstring becomes the LLM-visible description."""
    ...
```

**Watch out:** type-annotate generic containers fully — `list[dict]`, not
bare `list`. Codex (gpt-5.3 via Router) rejects function schemas where
an array parameter has no `items`. agentino derives the schema from the
Python annotation, so the annotation has to be specific.

## `agents.yml` (per-agent LLM config)

```yaml
providers:
  router:
    base_url: https://router.example.com/v1
    api_key: ${AI_API_KEY}

agents:
  <agent-id>:
    model: router/gpt-5.4-codex
    tools_dir: ./tools
```

`AI_API_KEY` resolves from the process env. **Provision a dedicated
key per app** — issue one from your model provider, store it outside the
repository, and reference it from `<app>/.env` as `AI_API_KEY`.

## `workspace.yml` (top-level)

```yaml
name: "My App"
icon: "🚚"
brand_color: "#4F46E5"
sidebar_color: "#1E1B4B"

apps:
  <agent-id>:
    name: "Display name"
    role: "What this agent does"
    avatar: "🚚"
    color: "#4F46E5"
    group: "default"
    type: "agentino"
    soul: "agents/<agent-id>/SOUL.md"
    tools: "agents/<agent-id>/tools/"

users:
  local:
    name: "You"
    role: "Owner"
    default: true
```

## Frontend (`web/src/app/page.tsx`)

Minimum viable page — fetch workspace config, render `WorkspaceShell`:

```tsx
'use client'
import { useEffect, useState } from 'react'
import type { AgentConfig } from '@agentino/workspace/pages/WorkspaceLayout'
import WorkspaceShell from '@agentino/workspace/pages/WorkspaceShell'
import { maybeSeedDemoChats } from '@/components/demoSeeds'

export default function Page() {
  const [cfg, setCfg] = useState<{ name: string; apps: AgentConfig[] } | null>(null)
  useEffect(() => {
    maybeSeedDemoChats()
    fetch('/api/workspace/config').then(r => r.json()).then(setCfg)
  }, [])
  if (!cfg) return null
  return <WorkspaceShell agents={cfg.apps} apiBase="/api/workspace"
    modes={['chat']} workspaceName={cfg.name} dashboards={[]}
    labels={RU_LABELS} />
}
```

## Demo dialog seeds (`web/src/components/demoSeeds.ts`)

Pattern: idempotent localStorage seed of a few pre-baked threads so first-time
visitors see populated sidebar. Bump `SEEDED_FLAG` suffix to force re-seed.
See `globex/web/src/components/demoSeeds.ts` for a fully worked example.

## Deploy (nginx example)

```nginx
# /etc/nginx/sites-enabled/<app>
server {
    server_name <app>.example.com;
    location / { proxy_pass http://127.0.0.1:<port>; ... }
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/<app>.example.com/fullchain.pem;
    ...
}
```

Check whether your certificate actually covers the new subdomain before
assuming it does — a certificate listing several names is a SAN list, not a
wildcard, and it will not match a name that was never added to it. Issue or
expand one per app with `certbot --nginx -d <app>.example.com`.

## Tests

Tools should have unit tests that exercise their `.fn` directly with a
tmp-path data dir. See `globex/tests/test_tools.py` for the pattern.
Integration tests for the full chat loop are out of scope for these — they
require Router live.

## Common gotchas

1. **`role: tool` rejected by Codex.** agentino's codex provider already
   converts `role: tool` → `function_call_output`. Don't roll your own LLM
   client — go through agentino.
2. **Bare `list` annotation.** Use `list[dict]` / `list[str]` so JSON schema
   has `items`.
3. **Tailwind globs.** Include `../../runspace/workspace/frontend/**`
   in your tailwind content so workspace classes don't get tree-shaken.
4. **`webpack.resolve.symlinks = false`.** Required when the workspace path
   contains node_modules symlinks.
5. **Subdomain cert.** A certificate covering several subdomains is usually
   a SAN list rather than a wildcard, so a new subdomain is not covered by
   it. Run `certbot --nginx -d <app>.example.com` for each new app.
6. **Background processes.** Use `nohup ... &` + `disown` and a systemd unit
   for prod. Bare `&` from a tool-driven shell may get killed when the shell
   returns.
