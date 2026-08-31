# `runspace.protocols` — the protocol layer

Six protocols that any tenant app builds on top of agentino. Each protocol has multiple
concrete implementations selected by env var or `set_*()` call at startup. ADR-0001
explains *why* this exists; this README explains *how to use it*.

## What's here

| Protocol | What it represents | Default impl |
|----------|--------------------|--------------|
| `Store` | CRUD over domain entities (invoices, bookings, customers, …) | `FileStore` (dev) / `SupabaseStore` (prod) |
| `Vision` | OCR + image understanding for invoice parsing | `CodexVision` (`gpt-5.3-codex` via Router) |
| `Transport` | Outbound delivery of messages (Telegram, WhatsApp, etc.) | `TelegramTransport` |
| `Clock` | "What's today?" — overrideable for time-travel tests | `WallClock` |
| `FileStorage` | Bytes in, URL out (Supabase Storage / local FS) | `LocalFileStorage` |
| `Embeddings` | Vector embeddings for semantic search | `RemoteEmbeddings` (Router-backed) |

## How tools use it

```python
from agentino import tool
from runspace.protocols import get_store

@tool(is_read_only=True)
async def list_invoices(status: str = "") -> dict:
    store = get_store()           # ← protocol, never a concrete class
    rows = store.list("invoices", tenant_id=...)
    return {"invoices": rows, "count": len(rows)}
```

The tool doesn't know whether `store` is a Supabase client or a JSON file — that's
chosen at startup by the host app.

## How the host app picks an impl

By environment, resolved when the adapter is first built:

```bash
export STORE_BACKEND=supabase        # file (default) | memory | supabase
export SUPABASE_URL=https://...
export SUPABASE_KEY=...
```

```bash
export STORE_BACKEND=file            # the default
export STORE_FILE_ROOT=/var/lib/app/store
```

That is the whole mechanism — there is no `set_store()`. Selection lives in
one place so the same image runs against fixtures in a sandbox and real
backends in production without a code branch, and so nothing can install a
different store halfway through a process's life.

Getters are cached, so a test that changes the environment has to clear them:

```python
from runspace.protocols import reset

monkeypatch.setenv("STORE_BACKEND", "memory")
reset()
```

To construct one directly — in a test, or when the host genuinely owns the
lifetime — import the implementation rather than the getter:

```python
from runspace.protocols.store import FileStore, InMemoryStore

store = FileStore(root=tmp_path)
```

## Why it exists (the short version)

Tenant apps used to import Supabase clients directly into tool code. That made tools
impossible to run in CI without Supabase, impossible to mock, and tightly bound to one
backend choice. The protocol layer:

- Lets sandboxed agents and tests run with `FileStore` / `InMemoryStore` (no network).
- Lets the migration from "everything in Supabase" to "file-as-truth where it belongs"
  happen one collection at a time.
- Makes the merge-gate `sandbox_lint.py` enforceable: `from supabase import` in tool
  code → CI fails.

Read `docs/adr/0001-adapter-pattern.md` for the full reasoning.

## The merge-gate

`sandbox_lint.py` runs in CI. It scans all `*/tools/*.py` paths and forbids:
- `from supabase import …`
- `from postgrest import …`
- Direct backend SDK imports (`boto3`, `redis`, …)
- Anything that requires network at import time

If you legitimately need a backend in a tool, you didn't — you need an adapter behind a
new protocol.

## Adding a new protocol

1. Decide on the public methods. Keep it small. `Store` has 5; `Vision` has 1.
2. Write `src/runspace/protocols/<your_protocol>/__init__.py` with the `Protocol`
   class (use `typing.Protocol`, structural typing).
3. Write at least an `InMemory<Protocol>` impl for tests.
4. Add `get_<your_protocol>()` to the registry in `src/runspace/protocols/registry.py`.
5. Wire env-var picker in `src/runspace/protocols/config.py`.
6. Contract tests in `src/runspace/protocols/tests/test_<your_protocol>.py` — one parameterised
   suite that runs against every concrete impl.
7. Update `docs/adr/0001-adapter-pattern.md` migration table.

## Don't add a protocol when

- It only has one impl and probably never will. (Helper module, not protocol.)
- The thing is a value object, not a behaviour. (Use a dataclass.)
- You're tempted to add a `WeatherProtocol` because tools call OpenWeatherMap. (That's
  a tool, not a protocol — keep it as a single function.)

## See also

- `docs/adr/0001-adapter-pattern.md` — the foundational ADR.
- `tests/` — contract test patterns to copy when adding a new protocol.
