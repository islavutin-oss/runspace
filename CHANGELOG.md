# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0 onward.

## [Unreleased]

### Added

- `cli_timeout_s` per app. A chat turn should give up quickly, a scheduled
  routine that calls four tools and writes a thousand words needs minutes; the
  global `RUNSPACE_CLI_TIMEOUT` served neither.

## [0.2.15] - 2026-09-13

### Added

- `models:` on an app maps caller role → model, falling back to `model`. The
  host labels the turn via `caller_role`; runspace never learns what an account
  is.
- `tools_cli` makes an agent's `@tool` modules callable from a shell, so a CLI
  runtime keeps its tools. The runtime writes the shim, carrying its own
  interpreter, import path and workspace context.
- `workspace_path:` per app, so agents whose tool names collide get separate
  dispatchers.
- `post_roles:` on a channel gates writes at the endpoint; `/config` reports
  `can_post`.
- `tool_labels:` names tools for the reader instead of for the model.

### Fixed

- Runtime failures no longer name the runtime or put stderr in a reply. All
  four adapters; detail goes to the log.
- Refused tool calls are dropped from `tools_used`. A call outside
  `cli_allowed_tools` is refused after the model asks for it, so the "Used:"
  line named tools that never ran.
- `tool_labels` reach hosts that consume `AppRegistry.chat_stream` directly;
  labelling was done in the gateway, so wrapping the registry lost it.
- A file named `..` no longer puts `..` in its file_id.

### Changed

- `caller_role` moved from the `claude_code` adapter to `backend/caller.py`;
  the gateway reads it and must not import a runtime.

## [0.2.2] - 2026-08-30

### Fixed

- Fenced ```table / ```datatable blocks render without a `|---|---|` separator
  row. Inside a fenced block the content is already declared a table; the
  separator is still required when sniffing tables out of free markdown.

### Changed

- Separator-less tables take their header from the longest run of rows with a
  consistent column count, so a lead-in sentence containing a pipe is not
  mistaken for the header.
- `\|` is an escaped pipe within a cell, not a column break.

## [0.2.1] - 2026-08-30

### Fixed

- `parseMarkdownTable` accepts tables with omitted outer pipes. The row test
  required a pipe at both ends, so valid GitHub-markdown tables parsed as
  nothing.

### Added

- The React UI ships inside the wheel at `runspace/workspace/frontend`.
  Consumers copy it into `node_modules/@runspace/ui` instead of symlinking a
  checkout, keeping the UI pinned to the Python version.
