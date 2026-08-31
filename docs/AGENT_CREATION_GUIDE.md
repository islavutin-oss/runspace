# How to add a new agent to a workspace

Two patterns that cover most agents. Pick the one that matches the role
you are building for.

For Pattern A specifically, the **third reference example** is Ada in
acme (`acme/platform/agents/accountant/`). Ada shows the
post-ADR-0001 shape: each tool is a thin wrapper (~30 lines) around a
**business pack** in `services/invoices/`, never importing supabase
or HTTP clients directly. See
`acme/platform/docs/INVOICE_PIPELINE.md` for the layered diagram.

> Read `NEW_APP_BOILERPLATE.md` first if you're spinning up a whole new app.
> This guide is just about adding agents to an *existing* WorkspaceGateway-
> backed app.

## Pattern A — operator-with-tools (one or more)

Agents that perform actions in your domain. Examples:

- **Analyst** (retail analytics): runs SQL, renders charts, looks up
  segments, exports SVG. ~12 tools across `agents/retail-analytics/tools/`.
- **Coordinator**: pings suppliers, marks asks
  received, creates cards, hands off to content. 10 tools.

```
agents/<agent-id>/
├── SOUL.md            # system prompt: role, rules, output style, viz blocks
├── agents.yml         # provider + model + tools_dir
└── tools/             # ONE @tool per file
    ├── action_one.py
    ├── action_two.py
    └── ...
```

### `SOUL.md` checklist

- Role definition (who this agent serves, in what voice).
- Rules (when to use which tool; refusal cases).
- Output style: language, length, mandatory fields per response.
- **Status / terminology table** if your domain has codes the LLM might
  leak (e.g. rendering an internal `stale` flag as the word a user expects).
- **Visualization blocks** the agent should produce: `kpi`, `chart`,
  `insight`, `datatable`, `mermaid`. With concrete JSON examples.
- **Deep-link conventions** for CTA buttons (e.g. `#card=<id>`).
- Hard rules with negative examples — the LLM follows them better when
  shown a wrong vs right pair than when given a guideline alone.

### `agents.yml`

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

### Tool file (`tools/<name>.py`)

```python
"""One-line summary of what this tool does."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agentino import tool
from myapp import store  # your own module, not runspace's


@tool(is_read_only=True)
async def list_things(status: str = "") -> list[dict]:
    """Docstring → LLM-visible description. Be precise; the LLM reads it
    to decide when to call. Mention parameter constraints inline."""
    ...
```

**Gotchas:**
- Annotate generic containers fully: `list[dict]`, not bare `list`. Codex
  rejects array params without `items` schema.
- Tool name = filename stem. Keep it short and verb-y (`list_cards`,
  `mark_received`, `remind_all_stuck`).
- One `@tool` per file. WorkspaceGateway discovers by file.
- Side-effect-free tools: `is_read_only=True` (allows parallel execution
  by some agentino code paths).
- `sys.path.insert(0, parents[3])` is the standard incantation to import
  your app's `api/` modules — every tool file does it.

### Register in `workspace.yml`

Add an entry under `apps:`:

```yaml
apps:
  <agent-id>:
    name: "Display name"
    role: "What this agent does"
    avatar: "🎯"
    color: "#4F46E5"
    group: "default"   # or your custom group, e.g. "operators"
    type: "agentino"
    soul: "agents/<agent-id>/SOUL.md"
    tools: "agents/<agent-id>/tools/"
```

### Tests

For each tool, exercise its `.fn` with a tmp-path data dir. See
`globex/tests/test_tools.py` for the loader pattern + 24+ examples.
For each typical user prompt → tool chain, write a scenario test (see
`globex/tests/test_scenarios.py`).

---

## Pattern B — persona (no tools, just personality)

Agents that play a character in a conversation. They do not modify state.
Examples:

- **globex suppliers**: 8 of them — `supplier-bosch`, `supplier-cersanit`,
  …. Each is a vendor-side menager with quirks (formal / slow / evasive).
  No tools — pure persona.

```
agents/<agent-id>/
├── SOUL.md            # persona prompt: who you are, quirks, style
├── agents.yml         # same as Pattern A
└── tools/             # empty (just .gitkeep)
    └── .gitkeep
```

### `SOUL.md` for personas — what to include

- **Identity**: name, role, company, category they work in.
- **Personality**: 2-4 sentences on quirks (formal? slow? evasive?
  obsessive? language patterns?).
- **Dependencies**: who they have to wait on (HQ, dept X, lawyers).
- **Stylistic floor**: a language constraint, e.g. "reply only in the
  workspace language, with no loanwords".
- **Behavioral rules**: never closes everything at once, partial answers,
  references colleagues, attachments via filename mentions.
- **Format constraint**: e.g. "never make tool calls — you are a
  conversational partner, not an operator".

### Generation script (globex pattern)

When you have many personas with the same shape, store them as data and
generate the dirs. See `globex/scripts/gen_suppliers.py` (the throwaway
generator) for a working example: a tuple per persona, a SOUL template
string, the script writes 8 dirs and the workspace.yml block.

---

## When to add a new tool vs. a new persona

| Trait | Operator with tools (A) | Persona (B) |
|---|---|---|
| Modifies your domain state | ✓ | ✗ |
| Runs SQL / API calls / file ops | ✓ | ✗ |
| Produces ` ```kpi `, ` ```chart ` blocks | ✓ | rare |
| Holds conversational character | secondary | primary |
| Maps to a real human role | "the operator" | "the vendor", "the customer" |
| Tools dir | populated | empty |

If the agent needs *both* (a vendor-side operator who can also look up SKUs),
mix: persona-style SOUL + a few read-only tools.

---

## Hard rules learned the hard way

1. **One `@tool` per file.** Multiple tools in one file confuse agentino's
   discovery — only the last `Tool` instance gets registered.
2. **Codex rejects bare `list`** — use `list[dict]` so JSON schema has `items`.
3. **Don't name tools in chat output.** SOUL Rule 0:
   > Never mention internal tool names in user-facing replies.
   > "ready_to_handoff returned an empty list" → "Nothing is ready yet."
4. **Persona language must be enforced.** Without an explicit "reply only in
   this language" instruction the persona will mix in foreign phrases.
5. **CTA hrefs must be valid deep links.** Empty `cta.href` makes a dead
   button. Either omit `cta` entirely or use a registered hash anchor
   (e.g. `#card=<id>`, `#focus=<id>`).
6. **Insist on action + report pattern.** SOUL Rule 1:
   > Perform the action through the tool immediately; do not promise it.
   Otherwise the model announces the action and never calls the tool.
