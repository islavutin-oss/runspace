# ADR-0001 — Adapter pattern for stores, vision, transport, LLM

- **Status**: Accepted (2026-04-30; foundation merged on `feat/adapter-pattern`)
- **Date**: 2026-04-30
- **Branch**: `runspace:feat/adapter-pattern`
- **Drivers**: globex session (4 days of building, ~7+ pivots, repeated
  «works on my machine» pain), acme-ada sandbox (third app rebuilding
  the same fixture/test scaffolding from scratch), upcoming integration
  of Ada into the acme platform (Telegram + Supabase + vision)
- **Supersedes**: ad-hoc per-app `Demo*Provider` and per-app fixture JSON

## Context

Today every acme/globex/sandbox tool reaches directly into a
concrete backend:

- `from app.db.supabase import get_client` — Supabase pinned in tool code
- `from services.pos import get_pos_provider` — POS singleton with hidden Demo/vendor switch
- `httpx.post('https://router.example.com/v1/codex/responses', ...)` — Codex pinned in tool code
- `with open('data/inbox.json')` — file path hardcoded

**Symptoms** (concrete, observed):

1. **Test pain.** Every tool's test re-builds a `tmp_path` fixture +
   monkey-patches `store.DATA`. Six different patterns across globex
   tests alone.
2. **Sandbox sprawl.** globex and acme-ada-sandbox each re-invent
   their own seed.py, file-based store, and demo-toggle logic. One consumer
   project does it differently again.
3. **Hidden coupling.** When a tool's signature is
   `process_invoice(card_id) -> dict`, you can't tell from the call site
   whether it'll hit Supabase, Router, the local filesystem, or
   all three. A tool that works in dev silently fails on prod when the
   env diverges (and we've seen this — the 2026-04-29 pos.yaml drift
   incident in acme's audit log).
4. **CI gating fails.** globex's `full_tests` job needs the private
   `agentino` repo + `AGENTINO_REPO_PAT`. Forks can't run it. The
   `store_tests` no-deps half exists *because* the tools depend on
   shared imports. If tools went through adapters with sandbox impls,
   the full suite would run anywhere.
5. **POS DemoProvider** has been re-discovered, re-debugged, and
   tightened **6 times** during the acme POS refactor (the
   "6-layer canary" series in the git log) — exactly because the
   demo/live switch was implicit, not declared as an interface.

## Decision

Every external dependency a tool uses goes through one of **four protocols**.
Each protocol has at least two implementations: `live` and `sandbox`.

| protocol    | what it abstracts                          | live impl                                        | sandbox impl                                         |
|-------------|--------------------------------------------|--------------------------------------------------|------------------------------------------------------|
| `Store`     | CRUD on domain entities                    | `SupabaseStore`                                  | `FileStore` (atomic JSON files in `data/`)           |
| `Vision`    | image/PDF page → structured fields         | `CodexVision` (gpt-5.3-codex via Router)   | `FixtureVision` (scripted JSON keyed off filename)   |
| `Transport` | inbound message + file ingestion           | `TelegramTransport`, `WhatsAppBridge`            | `FileInboxTransport` (drop files in `data/inbox/`)   |
| `Provider`  | external system query (POS, weather, etc.) | `VendorPOSProvider`, `OpenMeteoProvider`          | `DemoPOSProvider`, `FixtureWeatherProvider`          |

`Provider` is already real (acme POS has Demo/vendor). This ADR
generalizes the same shape across the other three.

A central registry resolves the right impl from one env var:

```python
# src/runspace/protocols/registry.py
from .store import Store, FileStore, SupabaseStore
from .vision import Vision, CodexVision, FixtureVision
# ...

def get_store() -> Store:
    mode = os.environ.get("APP_MODE", "live")
    return FileStore(Path(os.environ["DATA_DIR"])) if mode == "sandbox" else SupabaseStore(...)

def get_vision() -> Vision:
    mode = os.environ.get("APP_MODE", "live")
    return FixtureVision(Path("tests/fixtures/vision")) if mode == "sandbox" else CodexVision()
```

App code only ever does `store = get_store()`. Tools never import
concrete classes.

## The sandbox-mode contract (merge gate)

A tool is **mergeable** iff:

1. It imports adapters via `runspace.services.{get_store, get_vision, get_transport, get_provider}` only — never concrete classes from a backend SDK.
2. `APP_MODE=sandbox pytest <tool's tests>` exercises the tool end-to-end **without network, without DB, without LLM cost**.
3. A live-mode smoke test exists (≥ 4 lines), gated in CI behind a
   PAT/secret so contributors without prod access aren't blocked.
4. Sandbox fixtures live under `tests/fixtures/<service>/` with predictable filenames so a contributor can wire a new test in <5 min by dropping a fixture.

CI lints (`ruff`/`grep`) reject PRs that import banned symbols
(`supabase`, raw `httpx.AsyncClient` to Codex, `python-telegram-bot`)
inside `agents/*/tools/*.py` — those imports must live in adapter impls.

## Protocol sketches

### `Store`

```python
# src/runspace/protocols/store/__init__.py
from typing import Protocol, runtime_checkable, Any

@runtime_checkable
class Store(Protocol):
    """Generic CRUD over named collections.

    Collections are dict-like records keyed by a string `id` field.
    Implementations: FileStore (atomic JSON), SupabaseStore (table per
    collection). MUST be safe for concurrent reads; serialize writes
    however the impl prefers.
    """
    def list(self, collection: str) -> list[dict]: ...
    def get(self, collection: str, id: str) -> dict | None: ...
    def save(self, collection: str, record: dict) -> dict: ...
    def update(self, collection: str, id: str, **fields) -> dict | None: ...
    def delete(self, collection: str, id: str) -> bool: ...
    def query(self, collection: str, **predicate) -> list[dict]: ...
```

Domain wrappers — `Invoices`, `Cards`, `Suppliers` — sit on top, calling
`store.list("invoices")` etc. Tools work against the wrapper, not the
raw `Store`. This way the wrapper enforces schema / derives status / etc.

### `Vision`

```python
# src/runspace/protocols/vision/__init__.py
from pathlib import Path
from typing import Protocol

class Vision(Protocol):
    """Extract structured fields from a scanned image / PDF page.

    Implementations:
      - CodexVision: gpt-5.3-codex via Router (live, ~1.2s P50)
      - FixtureVision: returns scripted JSON keyed by filename for
        sandbox/test mode

    Returns dict with at least: confidence: float (0-1), raw: str.
    Domain-specific extractors call `extract_invoice` / `extract_receipt`
    which compose this primitive.
    """
    async def extract(self, image: Path | bytes, prompt: str) -> dict: ...
```

### `Transport`

```python
# src/runspace/protocols/transport/__init__.py
class Transport(Protocol):
    """Inbound message + file ingestion stream.

    Implementations:
      - TelegramTransport: Bot API webhook, downloads files via getFile
      - WhatsAppBridge: existing acme bridge container
      - FileInboxTransport: scans data/inbox/ on disk; sandbox

    Each impl pushes incoming messages into the registered channel via
    on_message callback. Files land in a Storage-backed location +
    referenced by file_id.
    """
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def on_message(self, callback) -> None: ...
    async def fetch_file(self, file_id: str) -> bytes: ...
```

### `Provider` (already exists, formalize)

```python
# src/runspace/protocols/provider/__init__.py
class POSProvider(Protocol):
    """Already implemented as DemoProvider/VendorProvider in acme.
    Pattern is the model for the other three protocols above."""
    def connect(self) -> bool: ...
    def get_daily_sales(self, date: date) -> dict: ...
    # ...
```

Generalize: `WeatherProvider`, `EmailProvider`, future external systems.

## Migration plan

| step | what | where | effort | enables |
|------|------|-------|--------|---------|
| 0 | Approve this ADR | review by 1 reviewer | 1h | rest |
| 1 | Implement `Store` protocol + `FileStore` + tests | runspace | 4h | proves the pattern |
| 2 | Implement `SupabaseStore` | runspace | 4h | live use |
| 3 | Migrate one acme tool (`get_pending_invoices`) to use `Store` | acme/platform | 2h | reference migration |
| 4 | Implement `Vision` protocol + Codex + Fixture | runspace | 4h | invoice OCR is the first user |
| 5 | Implement `Transport` protocol + Telegram + FileInbox | runspace | 6h | Ada goes from sandbox to platform |
| 6 | CI lint that bans backend imports in `agents/*/tools/*.py` | runspace CI workflow | 2h | enforce the rule |
| 7 | Migrate remaining acme tools (estimate ~12 tools) | acme/platform | 1 week | full benefit |
| 8 | Update `NEW_APP_BOILERPLATE.md` + `AGENT_CREATION_GUIDE.md` to mandate adapter pattern | runspace | 1h | future apps comply |

## Migration progress (as of 2026-05-01)

Steps 1–6 done. Step 7 (full sweep of remaining acme tools) is
in flight; the invoice business pack on the `feat/invoice-parsing-e2e`
branch is the second reference user of the pattern after the POS
cache canary.

| step | status | note |
|------|--------|------|
| 1. Store + FileStore + tests          | ✅ | 48 contract tests across FileStore + InMemoryStore |
| 2. SupabaseStore                      | ✅ | already in production via POS cache canary (day 5) |
| 3. First migrated tool                | ✅ | `pos_cache.py` migrated; passes contract + live tests |
| 4. Vision protocol + Codex + Fixture  | ✅ | feeds `services/invoices/extractor.py` |
| 5. Transport protocol + FileInbox     | ✅ | sandbox path lives at `acme-ada-sandbox/` |
| 6. CI lint                            | ✅ | enforced on globex + acme |
| 7. Remaining tools                    | 🟡 | `services/invoices/*` is the second reference |
| 8. Update boilerplate docs            | 🟡 | `NEW_APP_BOILERPLATE.md` updated; agent guide pending |

Property tests caught a real path-traversal bug in `FileStore` (a
collection name containing `..` escaped the store root). Fix +
regression test landed before merge.

## Tradeoffs we accept

- **Indirection cost**: one extra `from runspace.protocols import get_store` in each tool. Trivial.
- **Wrapper layer cost**: domain wrappers (`Invoices`, `Cards`) mean two layers — protocol + wrapper. Worth it: keeps protocol generic, keeps domain rules explicit.
- **CI lint complexity**: another check. ~50 lines of grep/ruff config; net win.

## Tradeoffs we reject

- "Use Supabase everywhere, no sandbox" — already broken (FB#10 in globex was a sandbox-only seed bug; would have been worse on Supabase).
- "Mock at the SDK level (mock supabase-py)" — fragile, drifts when SDK upgrades, doesn't help when running an app locally without supabase-py installed.
- "DI container (e.g. `dependency-injector`)" — heavyweight for a 4-protocol surface; manual factory functions are clearer.

## Out of scope

- **Nix flakes for environment reproducibility** — separate ADR-0002. Nix is the runtime/binary layer; this ADR is the code/interface layer. They're complementary.
- **Multi-tenant data isolation** — orthogonal; `Store` impls handle that internally (Supabase RLS or per-tenant subdirectory in `FileStore`).
- **Caching** — orthogonal; can be a `CachingStore` decorator over any other `Store`.

## Acceptance criteria for marking this ADR Accepted

1. `Store` protocol + `FileStore` + 1 migrated acme tool ship to main.
2. The migrated tool's tests pass under `APP_MODE=sandbox` with zero
   external services running.
3. The same tool's live-mode smoke test passes against the dev Supabase
   in CI.
4. A code review confirms the migrated tool reads cleanly without
   knowing what backend is wired.

## References

- Existing `DemoProvider` / `VendorProvider` in `acme/platform/services/pos/`
- 6-layer canary fix series in `acme` git log (April 2026)
- `globex/tests/test_store.py` — the ad-hoc fixture pattern this ADR replaces
- ADR-0002 (forthcoming) — Nix flakes for environment reproducibility
