# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0
onward.

## [Unreleased]

### Added

- A SQLite messaging backend, now the default. Channels, threads, direct
  messages and read state previously required `SUPABASE_URL` and
  `SUPABASE_KEY`: without them `MessagingService` was never constructed, every
  channel route returned 404 and nothing persisted. Since channels are the
  workspace's main surface, a fresh install looked broken rather than
  unconfigured. Supabase is still used when it is configured.
- `InsightCard` reports a payload that parsed but is not an insight, naming
  the keys it did receive. It previously rendered an empty card, which reads
  as a layout bug rather than a malformed block.

- Apache-2.0 licence, `NOTICE`, contribution guide, security policy, code of
  conduct, and issue and pull-request templates.
- `response_filter` in `workspace.yml`: a dotted path to a callable the
  application owns. Runspace resolves and calls it without knowing what it
  checks.
- `max_turns` as per-app configuration, defaulting to 10.
- `gpt-5.4-codex` is the default model for the agent runtimes and vision.
- `WorkspaceRegistry.mount(app)`: exposes every tenant's gateway routes on one
  application, resolving the tenant per request. A single-tenant host could
  already do `app.include_router(gateway.router)`; a multi-tenant one had no
  supported way, so hosts hand-wrote a handful of routes and quietly exposed a
  fraction of the surface — channels, messages, uploads, pairings and the
  external-channel routes simply had no route at all. The documentation
  described this method before it existed.
- Built-in `gateway_status` and `schedule` settings widgets. Both types were
  named in `SettingsPage`'s union from the start and neither had an
  implementation, so a section declaring either rendered "Unknown widget
  type". An application can still replace either by passing a widget of the
  same name.
- Opening suggestions in `workspace.yml`, carried through `/config`, so a
  dialog client shows the workspace's own questions rather than generic ones.
- `py.typed`. Without it a type checker ignores every annotation in the
  package, so a hundred annotated modules did nothing for a consumer.

### Changed

- **Breaking: one import root.** The packages moved under `src/runspace/`, so
  everything is imported as `runspace.*`. The distribution previously installed
  `contracts`, `protocols`, `workspace`, `ingestion`, `helpers` and
  `runspace_cli` as top-level names, which would collide with a user's own
  modules. Consumers update with:

      from protocols import get_store        ->  from runspace.protocols import get_store
      from workspace.backend import ...      ->  from runspace.workspace.backend import ...

  No compatibility shim ships. Aliasing the old names in `sys.modules` made
  submodule imports execute a second time under a second name, giving two
  module objects with separate caches — a worse failure than an ImportError.

  `workspace/frontend/` stays at the repository root: it is the `@runspace/ui`
  npm package, not part of the Python tree.
- Zero-configuration defaults. `STORE_BACKEND` defaults to `file` (rooted at
  `./.runspace/store`), `VISION_BACKEND` to `fixture` and `TRANSPORT_BACKEND`
  to `file`, so a fresh install runs with no environment set. Previously the
  store defaulted to `supabase` and raised on import without credentials.
  Deployments relying on the old defaults must set them explicitly.
- Licence changed from MIT to Apache-2.0. The repository previously declared
  MIT in metadata with no licence file present.
- Environment variables are no longer named after particular deployments. The
  embeddings and vision adapters read `EMBEDDINGS_*`, `AI_BASE_URL`,
  `AI_API_KEY` and `VISION_API_KEY`.
- CI runs `ruff`, covers Python 3.10 through 3.13, exercises all six test
  directories rather than one, builds the distribution with a `twine`
  metadata check, and runs the frontend parser tests.

### Fixed

- The distribution declared a dependency on `agentino`, which on PyPI is an
  unrelated project. Neither package is published to PyPI; install is from git. The agent runtime is now the `[agentino]` extra, pulling
  `agentino-framework` — Runspace routes each turn to whichever runtime an app
  declares and requires none of them, so it does not belong in the core
  requirements.
- `pip install runspace` produced an install that could not be imported:
  `contracts.scheduling` needs `croniter` and `protocols.config` needs
  `pydantic-settings`, both of which had been arriving transitively through
  agentino. Both are now declared. Verified by installing the wheel into an
  empty virtualenv with no agent runtime present.

- `gateway.py` used `Any` in six annotations without importing it, which
  raises under `typing.get_type_hints()`.
- The multi-tenant isolation property tests shared a single store across every
  hypothesis example. Rows accumulated, so a tenant id generated as `tenant_a`
  in one example could reappear as `tenant_b` in a later one and read as a
  leak. Each example now builds its own store.
- `tests/test_telegram_render.py` imported a package path that never resolved,
  so the module could not be collected at all.
- A test fixture repeated the `token` key in one dictionary literal.
- A test drove its coroutines through `asyncio.get_event_loop()`, which on
  Python 3.12 only works when an earlier test left a loop on the thread, so it
  passed or failed depending on collection order.
- The `[supabase]` extra was documented but never declared, and `[documents]`
  omitted pymupdf, which the scanned-PDF path imports.
- The widget-integrity regex covered `chart`, `datatable` and `kpi` but not
  `insight`, which the frontend also renders. An insight block the model
  mangled was never spliced back over — the canonical copy was appended at
  the end instead, so the page showed a parse error above the real card.
- Seventeen test modules computed a `sys.path` entry from `parents[N]`. The
  indices were wrong after the move, and one of them put `src/runspace` on the
  path, making every subpackage importable a second time as a top-level name.
  Path setup now lives once in the root `conftest.py`.
- The agent answered twice in a channel. Posting to `/channels/{slug}/messages`
  dispatches `@mentions`, and the channel UI also streams its own turn, so once
  the channel routes were reachable every mention produced two model calls and
  two stored replies. The POST now honours `dispatch=false`.
- `runspace init` produced a workspace that would not boot. The generated
  config named `tenants.<id>.plugins.health`, which is not an importable module
  path when the id contains a hyphen and assumed a working directory the
  printed instructions do not use. The prompts also raised an unhandled
  `EOFError` when stdin was not a tty, so the command died half-scaffolded in
  CI or a Dockerfile.
- The `sandbox_lint` CI step ran `python -m protocols.sandbox_lint`, a module
  path the rename removed, so that job could never have passed.
- The documented development setup produced a checkout whose tests could not
  collect, and `[dev]` named a package that is not on PyPI, so the obvious
  correction failed outright.
- Eight tests silently skipped because they looked for the frontend inside the
  Python package and named a layer that had been renamed. Two layering rules
  ran green while checking a directory that no longer existed, so a shared
  component importing from `team/` passed the rule forbidding exactly that.
- Twenty file paths named in the documentation pointed at files that are not
  in the repository, including a package renamed two versions ago.
- Tests shared one storage root and one adapter registry, so state leaked
  between them. An autouse fixture now gives each test its own roots and
  resets the cached adapters.

### Removed

- The `packages/` split distributions. `packages/runspace` claimed the same
  PyPI name as the repository root with an older version and a subset of the
  code, so only one of them could ever own that name. Both also produced
  broken source distributions: their `force-include` pointed at `../../src`,
  which does not exist inside an extracted sdist, so a source install failed
  to build a wheel. The root distribution supersedes both.

- Product-specific policy from the shared runtime: hardcoded nudge strings in
  one natural language, refusal markers, a datatable row cap naming one
  application's chart tools, and a fixed `max_turns=25` applied to every app.
- Internal planning documents that tracked a migration through internal
  phases and named host paths, ticket numbers and unshipped projects.
- Twelve test guards that read a directory which had moved to
  `agentino.tools.std`. The equivalent guards now live in that repository.
## [0.2.2] - 2026-08-30

### Fixed

- A ```table / ```datatable block whose markdown table has no `|---|---|`
  separator row now renders. Models routinely emit a header and rows and omit
  the separator; the parser required it, returned null, and the reader saw
  "Invalid table data: could not parse as JSON or markdown table" underneath an
  otherwise correct answer. Inside a fenced table block the author has already
  declared the content is a table, so the separator is no longer needed to tell
  a table from prose — it is still required when sniffing tables out of free
  markdown, where it remains the only discriminator.

### Changed

- Separator-less tables pick their header from the longest run of consecutive
  rows with a consistent column count, so a lead-in sentence containing a pipe
  is not mistaken for the header and trailing prose is not parsed as a row.
- `\|` is treated as an escaped pipe within a cell rather than a column break.

## [0.2.1] - 2026-08-30

### Fixed

- `parseMarkdownTable` accepts tables whose outer pipes are omitted. GitHub
  markdown treats `Ветвь | Доля` as a valid table, but the row test required a
  pipe at *both* ends, so such a table parsed as nothing and the reader was
  shown "Invalid table data: could not parse as JSON or markdown table" for a
  table that was never malformed. Reported against a Russian-language agent
  reply. The separator row remains the discriminator, so prose containing a
  pipe is still refused.

### Added

- The React UI ships inside the wheel, at `runspace/workspace/frontend`.
  `pip install runspace[workspace]` is now the whole dependency: consumers copy
  that directory into `node_modules/@runspace/ui` instead of symlinking a
  checkout, which keeps the UI pinned to the same version as the Python side
  and lets a deployment drop its source clone entirely.

