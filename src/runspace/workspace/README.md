# Agentino Workspace

Slack-like back-office shell for AI agent teams. Agents connect as apps — no code changes needed.

## Quick Start

### 1. Backend (Python)

```python
from runspace.workspace.backend import WorkspaceGateway

gw = WorkspaceGateway.from_config("workspace.yml")
app.include_router(gw.router)
```

### 2. Config (`workspace.yml`)

```yaml
name: "My Back Office"

apps:
  assistant:
    name: Assistant
    role: Support Agent
    avatar: "🤖"
    color: "#6B7280"
    type: agentino
    soul: assistant/SOUL.md
    tools: assistant/tools/

routines: routines.yml
```

### 3. Frontend (Next.js)

Copy `frontend/components/` and `frontend/pages/` into your app. Configure the sidebar:

```tsx
import { Sidebar, type SidebarConfig } from '@/components/workspace'

const config: SidebarConfig = {
  appName: 'My App',
  basePath: '/workspace',
  channels: [
    { id: 'general', label: 'General', icon: 'Hash', href: '/workspace/general' },
  ],
  agentGroups: [
    { label: 'AI Team', agents: [/* from workspace.yml */] },
  ],
}
```

## Features

- **Agent Gateway** — agents connect as apps (agentino, HTTP, webhook)
- **Slack-like Chat** — @mentions, threads, formatting toolbar, SSE streaming
- **Tool Visualization** — real-time display of which tools agents are calling
- **Activity Log** — audit trail of every agent action
- **Routines** — scheduled agent runs with cron, YAML-based config
- **Config-driven** — workspace.yml defines everything, no code needed

## Architecture

```
workspace.yml          → defines which agents connect
  ↓
WorkspaceGateway       → FastAPI router with /chat, /stream, /activity, /routines
  ↓
AppRegistry            → manages agent lifecycle, routing, sessions
  ↓
Agent (agentino/http)  → executes tools, returns responses
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/workspace/apps | List connected agents |
| POST | /api/workspace/chat | Chat with an agent |
| POST | /api/workspace/chat/stream | SSE streaming chat |
| GET | /api/workspace/activity | Activity audit log |
| GET | /api/workspace/routines | List scheduled routines |
| POST | /api/workspace/routines/{id}/run | Trigger a routine |
