"""SOUL.md flattening — include resolution + placeholder substitution.

Same algorithm both agentino's AppRegistry and openclaw's sync script
must run. Migrated from `runspace/workspace/backend/app_registry.py`
(2026-05-06). Pure functions; reads files but otherwise stateless.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_INCLUDE_RE = re.compile(r"\{\{include:([^}]+)\}\}")
_MAX_DEPTH = 3  # nested includes; same limit as the historic
# _resolve_soul_includes implementation


def resolve_includes(text: str, base_dir: Path, *, max_depth: int = _MAX_DEPTH) -> str:
    """Expand `{{include:<rel-or-abs-path>}}` directives.

    - Relative paths resolve from `base_dir` (typically the SOUL.md's parent).
    - Absolute paths used as-is.
    - Missing files → empty string with a logged warning. The agent
      still loads; we never raise here. The 2026-05-06 SOUL-loading
      hardening lives at `AppRegistry.register` (raises on the
      top-level SOUL not existing); within nested includes we tolerate
      misses to avoid blocking on a stale optional partial.
    - Recurses up to `max_depth` levels.
    """
    return _resolve_one(text, base_dir, depth=0, max_depth=max_depth)


_PKG_SCHEME = "runspace:"


def _package_template(name: str) -> Path:
    """Resolve `{{include:runspace:<name>}}` to a partial shipped with runspace.

    The block vocabulary a SOUL has to describe — chart, datatable, kpi,
    insight, mermaid — is defined by the frontend that renders it, which ships
    here. Restating it in every workspace's SOUL is how it drifts: the demo
    SOULs listed eight chart types while the renderer had ten, so two were
    unreachable to any agent that only read its own prompt.

    Traversal outside the templates directory is refused. An include path is
    workspace-authored, and a workspace should not be able to name
    `../../../etc/passwd` and have it flattened into a prompt.
    """
    root = (Path(__file__).resolve().parents[2] / "templates").resolve()
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        log.warning("SOUL include escaped the template directory: %s", name)
        return root / "__refused__"
    return candidate


def _resolve_one(text: str, base_dir: Path, *, depth: int, max_depth: int) -> str:
    if depth >= max_depth:
        return text

    def _sub(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        if raw.startswith(_PKG_SCHEME):
            ref = _package_template(raw[len(_PKG_SCHEME) :])
        else:
            ref = Path(raw)
            if not ref.is_absolute():
                ref = (base_dir / raw).resolve()
        try:
            if not ref.exists():
                log.warning("SOUL include not found: %s (from %s)", ref, base_dir)
                return ""
            content = ref.read_text(encoding="utf-8")
            return _resolve_one(content, ref.parent, depth=depth + 1, max_depth=max_depth)
        except Exception as e:
            log.warning("SOUL include failed for %s: %s", ref, e)
            return ""

    return _INCLUDE_RE.sub(_sub, text)


def flatten_soul(
    soul_path: Path,
    *,
    persona_name: str,
    tenant_name: str,
    max_depth: int = _MAX_DEPTH,
) -> str:
    """Read SOUL.md, strip front-matter, resolve includes, substitute placeholders.

    Output is the agent's full system-prompt text — the same string both
    agentino loads into `Agent.instructions` and openclaw inlines as
    `systemPromptOverride` in its config.

    Front-matter handling: a `---\\n...---` block at the very start is
    treated as YAML metadata and dropped (matches existing AppRegistry
    behavior, see app_registry.py 2026-05).
    """
    if not soul_path.exists():
        raise FileNotFoundError(
            f"SOUL.md not found: {soul_path}. "
            f"Fix the soul: path in workspace.yml so it resolves to an existing file."
        )
    text = soul_path.read_text(encoding="utf-8")

    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3 :].strip()

    text = resolve_includes(text, soul_path.parent, max_depth=max_depth)
    text = text.replace("{{persona_name}}", persona_name)
    text = text.replace("{{tenant_name}}", tenant_name)

    if not text.strip():
        raise ValueError(
            f"SOUL.md flattened to empty for {soul_path}. "
            f"Likely all `{{{{include:...}}}}` resolutions failed or the file "
            f"contained only front-matter."
        )
    return text
