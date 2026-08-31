# ADR-0002 — mcp-ui block integrity (charts survive the LLM)

**Date:** 2026-05-21
**Status:** Adopted
**Scope:** `src/runspace/workspace/backend` — all agentino-runtime mcp-ui consumers
(any host application)

## Problem

Tools emit mcp-ui widgets as fenced blocks in their return string:

````
```chart
{"type":"heatmap","data":[…100 cells…],"xKey":"country","yKey":"class","valueKey":"n","columns":[…],"rows":[…]}
```
````

The React workspace (`@runspace/ui` `InlineChart` / `InlineTable`) parses
those fences and renders interactive widgets. But the block has to travel
through the LLM's final reply, and **the model corrupts large payloads**:

1. **Rewrites / truncates.** Asked to reproduce a 100-cell heatmap or a
   1700-point scatter, the model drops cells and required keys (`xKey`,
   `columns`, …). The widget renders empty (all `–`).
2. **Can't echo tokens.** Our first fix emitted a short `{"$ref":"…"}`
   token for the model to copy verbatim. The model *fabricated a different
   token* — LLMs don't reliably transcribe random hex.
3. **Runtime truncates tool events.** The agentino `TOOL_RESULT` event is
   capped (~4 KB), so even the canonical tool output captured in
   `tool_outputs` is cut mid-block (no closing fence). Splicing from
   `tool_outputs` is therefore unreliable.

Net: neither the full block nor a token round-trips through the model.

## Decision

Do not depend on model fidelity at all. **Buffer canonical blocks per turn
and splice them positionally after the agent loop.**

- `src/runspace/workspace/backend/_mcp_ui.py` owns a process-global, lock-guarded
  per-turn list (`begin_turn` / `register_block` / `restore_mcp_ui_blocks`).
  It is a plain global, **not a `contextvar`**, because agentino runs sync
  `@tool` functions in a thread pool and contextvar bindings don't
  propagate into those threads — the tool would append to a throwaway copy.
- A tool's helper calls `register_block(full_block)`, which stores the full
  block and returns a tiny placeholder fence (`{"$mcpui": <idx>}`) for the
  model to position in its prose.
- The agentino runtime (`src/runspace/workspace/backend/runtimes/agentino.py`) calls `begin_turn()` before
  the agent runs and `restore_mcp_ui_blocks(reply, tool_outputs)` **once**,
  right before `_add_to_history` / return, on both the non-stream `chat`
  and the `stream` path. Restore replaces each model-emitted fence with the
  canonical block of the same type in emission order, and appends any the
  model dropped. Token/echo fidelity is irrelevant.

## Critical pitfall: restore exactly once

`restore_mcp_ui_blocks` consumes (clears) the turn buffer. **Do not call it
again downstream** (e.g. in `gateway.py`'s chat handler). The second call
sees an empty buffer, falls back to `tool_outputs` — which now holds the
*placeholder* the tool emitted, not the full block — and re-injects the
placeholder over the already-correct canonical block. The gateway must use
`result["text"]` as-is. (This bug cost a long debug session on
2026-05-21: runtime produced clean 2073-char text, gateway double-restore
reverted it to a 956-char placeholder.)

## Tool authoring contract

A chart tool returns:

```python
from runspace.workspace.backend._mcp_ui import register_block
full = f"```chart\n{json.dumps(cfg, ensure_ascii=False)}\n```"
marker = register_block(full)        # tiny placeholder fence
return f"{intro}\n{marker}\n"        # model echoes intro + marker
```

Fallback: if the import fails (older runtime without the store), emit the
full block inline — degrades to the old best-effort behaviour.

## Consequences

- Charts of any size render correctly regardless of model behaviour.
- The model still controls *placement* (it positions the placeholder) and
  *prose* (analysis around the chart) — only the block payload is pinned.
- Supported fence types: `chart`, `datatable`, `kpi`. Scatter was added to
  `InlineChart` / `parseChartConfig` alongside this work.
- Second runtimes (codex, claude_code, openclaw) don't yet call
  `begin_turn`; their replies fall back to the `tool_outputs` recovery path
  (works only when the tool output wasn't truncated). Wire `begin_turn` +
  `restore_mcp_ui_blocks` into a runtime when it needs large-widget support.
```
