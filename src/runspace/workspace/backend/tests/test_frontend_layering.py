"""Pin frontend layering + barrel rules."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# The frontend is an npm package and deliberately sits outside the Python
# tree, at <repo>/workspace/frontend — not under src/runspace/. parents[2]
# pointed inside the package, so every check here skipped as "not present
# in this checkout" while the files it guards were right there.
_FRONTEND = Path(__file__).resolve().parents[5] / "workspace" / "frontend"


# ─────────────────────────────────────────────────────────────────────────
# Rule 1 — shared/ doesn't import upward
# ─────────────────────────────────────────────────────────────────────────


def _scan_imports(directory: Path) -> list[tuple[Path, str]]:
    """Return (file, import_line) pairs for all relative imports in `directory`."""
    out = []
    if not directory.exists():
        return out
    for f in list(directory.rglob("*.tsx")) + list(directory.rglob("*.ts")):
        if "__tests__" in f.parts or f.name.endswith(".test.ts") or f.name.endswith(".test.tsx"):
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"""(?:from|require\()\s*['"]([^'"]+)['"]""", text):
            out.append((f, m.group(1)))
    return out


def test_shared_does_not_import_from_team():
    """shared/* must not import from team/*.

    Team mode is a consumer of shared, not the other way around. This rule
    named the layer `workspace/` until it was renamed to `team/`, so it ran
    green against a directory that no longer existed and could not have caught
    a violation.
    """
    bad = []
    for f, imp in _scan_imports(_FRONTEND / "shared"):
        if re.search(r"\.\./.*\bteam/", imp) and "/shared/" not in imp:
            bad.append(f"{f.relative_to(_FRONTEND)}: {imp}")
    assert not bad, (
        "Layering violation — shared/ imports from team/:\n  "
        + "\n  ".join(bad)
        + "\n\nshared/ is the base layer. If a component lives in team/ "
        "and shared/ needs it, the component is mis-categorized — move it to "
        "shared/ instead."
    )


def test_shared_does_not_import_from_dialog():
    """Same rule as above for dialog/."""
    bad = []
    for f, imp in _scan_imports(_FRONTEND / "shared"):
        if re.search(r"\.\./.*\bdialog/", imp) and "/shared/" not in imp:
            bad.append(f"{f.relative_to(_FRONTEND)}: {imp}")
    assert not bad, "Layering violation — shared/ imports from dialog/:\n  " + "\n  ".join(bad)


# ─────────────────────────────────────────────────────────────────────────
# Rule 2 — barrel exports must exist in the barrel's directory
# ─────────────────────────────────────────────────────────────────────────


def _check_barrel(barrel_path: Path) -> list[str]:
    """Return list of broken `from './X'` exports in this barrel."""
    if not barrel_path.exists():
        return []
    text = barrel_path.read_text(encoding="utf-8")
    broken = []
    # Match `from './<name>'` (relative-sibling exports)
    for m in re.finditer(r"""from\s+['"]\.\/([\w/]+)['"]""", text):
        name = m.group(1)
        candidates = [
            barrel_path.parent / f"{name}.tsx",
            barrel_path.parent / f"{name}.ts",
            barrel_path.parent / name / "index.tsx",
            barrel_path.parent / name / "index.ts",
        ]
        if not any(c.exists() for c in candidates):
            broken.append(name)
    return broken


@pytest.mark.parametrize(
    "barrel_rel",
    [
        "shared/components/index.ts",
        "team/components/index.ts",
        "team/pages/index.ts",
        "dialog/components/index.ts",
        "dialog/pages/index.ts",
    ],
)
def test_barrel_re_exports_only_existing_files(barrel_rel: str):
    """Each barrel `index.ts` must only re-export files that exist
    next to it. Bare `from './X'` is sibling-relative; the file must
    be in the same directory."""
    barrel = _FRONTEND / barrel_rel
    if not barrel.exists():
        pytest.skip(f"Barrel {barrel_rel} not present in this checkout")
    broken = _check_barrel(barrel)
    assert not broken, (
        f"Barrel {barrel_rel} re-exports symbols whose files are missing "
        f"from {barrel.parent}:\n  "
        + "\n  ".join(broken)
        + "\n\nThis usually means the file moved during a reorg but the "
        "barrel still points at the old path. Either move the file back "
        "or drop the export from the barrel."
    )


# ─────────────────────────────────────────────────────────────────────────
# Smoke — every barrel-imported symbol used by sibling files actually exists
# ─────────────────────────────────────────────────────────────────────────


def test_team_pages_dont_import_threadpanel_from_shared_barrel():
    """Regression: AgentChat once did `import { ThreadPanel } from
    '../../shared/components'` but ThreadPanel is team-only.
    The barrel-completeness test above catches it from the barrel
    side; this catches it from the consumer side."""
    # The layer was renamed workspace/ → team/; this skipped ever since.
    workspace_pages = _FRONTEND / "team" / "pages"
    if not workspace_pages.exists():
        pytest.skip("workspace/pages not present")
    bad = []
    for f in workspace_pages.glob("*.tsx"):
        text = f.read_text(encoding="utf-8")
        # Look for shared-barrel imports that include workspace-only symbols
        for m in re.finditer(
            r"""import\s*\{([^}]+)\}\s*from\s*['"]\.\.\/\.\.\/shared\/components['"]""",
            text,
        ):
            symbols = [s.strip() for s in m.group(1).split(",")]
            for sym in symbols:
                # ThreadPanel, KPICard, Sidebar, etc. — workspace-only
                if sym.lstrip("type ").strip() in {
                    "ThreadPanel",
                    "Sidebar",
                    "ResizeHandle",
                    "ModeSwitcher",
                    "DashboardPanel",
                    "Kanban",
                }:
                    bad.append(f"{f.name}: {sym} pulled from shared barrel")
    assert not bad, (
        "Workspace-only symbol pulled from shared barrel:\n  "
        + "\n  ".join(bad)
        + "\n\nImport these from '../components/<Name>' (workspace-local), "
        "not from the shared barrel."
    )
