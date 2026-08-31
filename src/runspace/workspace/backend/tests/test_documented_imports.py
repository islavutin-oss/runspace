"""Every first-party import the shipped markdown shows must resolve."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4].parent
_FIRST_PARTY = {"agentino", "runspace"}


def _markdown() -> list[Path]:
    seen: list[Path] = []
    for pattern in ("*.md", "docs/**/*.md", "src/**/*.md"):
        seen.extend(_ROOT.glob(pattern))
    return sorted(set(seen))


def _runnable_files() -> list[Path]:
    """Files that name a module in a command someone or something runs.

    Workflows are the reason this exists: CI invoked
    `python -m protocols.sandbox_lint`, a path the rename had removed, and it
    would have failed on the first run after publication. Documentation checks
    never looked at yaml."""
    seen: list[Path] = []
    for pattern in (".github/workflows/*.yml", ".github/workflows/*.yaml", "Makefile", "*.toml"):
        seen.extend(_ROOT.glob(pattern))
    return sorted(set(seen))


def _claims() -> list[tuple[str, str, str]]:
    out = []
    for f in _markdown():
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            continue
        for m in re.finditer(r"^from ([\w.]+) import ([\w, ]+)", text, re.M):
            module = m.group(1)
            if module.split(".")[0] not in _FIRST_PARTY:
                continue
            for name in (n.split("#")[0].strip() for n in m.group(2).split(",")):
                if name:
                    out.append((str(f.relative_to(_ROOT)), module, name))
    return out


def test_some_claims_were_found():
    """Guards the parametrised test from passing because the glob broke."""
    assert _markdown(), f"no markdown found under {_ROOT}"
    assert _claims(), "no first-party imports parsed — has the doc format changed?"


@pytest.mark.parametrize("page,module,name", _claims(), ids=lambda v: str(v)[:44])
def test_documented_import_resolves(page, module, name):
    try:
        mod = importlib.import_module(module)
    except ImportError as e:
        pytest.skip(f"{module} needs an extra not installed here: {e}")
    assert hasattr(mod, name), (
        f"{page} tells a reader to write `from {module} import {name}`, which does not exist"
    )


# Module names that existed before the package was renamed. A doc that still
# uses one is not caught by the check above, because the filter above only
# looks at `agentino.*` and `runspace.*` — a stale name simply drops out of
# the parametrisation and the coverage silently shrinks. So name them.
_RETIRED = {
    "ag_services": "runspace.protocols",
    "channels": "runspace.ingestion",
    "agentino.tool": "agentino",
    "agentino.message": "agentino",
    "workspace.serve": "runspace.workspace.serve",
    "workspace.backend": "runspace.workspace.backend",
    "protocols": "runspace.protocols",
}


@pytest.mark.parametrize("dead,replacement", sorted(_RETIRED.items()))
def test_no_document_uses_a_retired_module_name(dead, replacement):
    hits = []
    for f in _markdown():
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            continue
        for m in re.finditer(r"^(?:from|import) ([\w.]+)", text, re.M):
            mod = m.group(1)
            if mod == dead or mod.startswith(dead + "."):
                hits.append(f"{f.relative_to(_ROOT)}: {mod}")
    assert not hits, f"{dead!r} was renamed to {replacement!r}; still referenced in " + ", ".join(
        hits
    )


@pytest.mark.parametrize("dead,replacement", sorted(_RETIRED.items()))
def test_no_workflow_runs_a_retired_module(dead, replacement):
    """`python -m <module>` in CI, a Makefile or pyproject scripts."""
    hits = []
    for f in _runnable_files():
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            continue
        for m in re.finditer(r"-m\s+([\w.]+)|[\"']([\w.]+):[\w.]+[\"']", text):
            mod = m.group(1) or m.group(2) or ""
            if mod == dead or mod.startswith(dead + "."):
                hits.append(f"{f.relative_to(_ROOT)}: {mod}")
    assert not hits, f"{dead!r} was renamed to {replacement!r}; still invoked in " + ", ".join(hits)


# A path named in prose is a promise that the file is there. These are the
# ones that point inside this repository — guides also name application-side
# files a reader is meant to own, and those are not ours to resolve.
_FIRST_PARTY_PREFIXES = (
    "src/",
    "workspace/",
    "docs/",
    "tests/",
    "scripts/",
    ".github/",
)


def _prose_paths() -> list[tuple[str, str]]:
    out = []
    for f in _markdown():
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            continue
        for m in re.finditer(r"`([A-Za-z0-9_./-]+\.(?:py|md|ts|tsx|yml|yaml|toml|mjs))`", text):
            target = m.group(1)
            if target.startswith(_FIRST_PARTY_PREFIXES):
                out.append((str(f.relative_to(_ROOT)), target))
    return sorted(set(out))


def test_some_prose_paths_were_found():
    assert _prose_paths(), "no first-party file paths parsed — has the doc format changed?"


@pytest.mark.parametrize("page,target", _prose_paths(), ids=lambda v: str(v)[:46])
def test_a_file_named_in_prose_exists(page, target):
    """A rename leaves these behind silently. `ag_services/registry.py` and
    `tests/test_scheduler_protocol.py` both outlived the files they named."""
    assert (_ROOT / target).exists(), f"{page} names {target}, which is not in the repository"


def test_the_package_is_marked_as_typed():
    """PEP 561: without a `py.typed` marker inside the package, a type checker
    ignores every annotation in it — over a hundred annotated modules here
    would silently do nothing for a consumer, and the classifier claiming
    `Typing :: Typed` would be a lie."""
    try:
        import tomllib
    except ModuleNotFoundError:  # tomllib is 3.11+; the project supports 3.10
        import tomli as tomllib

    assert (_ROOT / "src" / "runspace" / "py.typed").exists(), "py.typed marker missing"
    meta = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "Typing :: Typed" in meta["project"]["classifiers"], (
        "the marker ships but the classifier does not advertise it"
    )


def test_the_readme_and_contributing_give_the_same_setup():
    """Two files told a newcomer two different things: the README's install
    line was corrected and CONTRIBUTING's was not, so which one you followed
    decided whether your tests could collect.

    Both used to have to name agentino explicitly, because it was not on PyPI
    and `[dev]` could not declare it. Since 2026-08-29 it can, so the check is
    that both files point at the same one-line install."""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for f, text in (("README.md", readme), ("CONTRIBUTING.md", contributing)):
        assert 'pip install -e ".[dev]"' in text, f"{f} does not use the dev extra"
        assert "agentino-framework[docgen] @ git+" not in text, (
            f"{f} still installs agentino from git; it is on PyPI and `[dev]` declares it"
        )
        assert "pip install pytest pytest-asyncio" not in text, (
            f"{f} still hand-lists test tools — that install produces a checkout "
            "whose tests cannot collect"
        )


def test_the_security_policy_does_not_contradict_the_version():
    """A supported-versions section claiming pre-1.0 on a 1.x release tells a
    reader the wrong thing about what gets patched."""
    try:
        import tomllib
    except ModuleNotFoundError:  # tomllib is 3.11+; the project supports 3.10
        import tomli as tomllib

    version = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    policy = (_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    if not version.startswith("0."):
        assert "pre-1.0" not in policy, f"SECURITY.md says pre-1.0 but the package is {version}"


def _rendered_fences() -> set[str]:
    """Fence languages the markdown renderer actually dispatches to a component."""
    src = (
        _ROOT / "workspace" / "frontend" / "shared" / "components" / "MarkdownContent.tsx"
    ).read_text(encoding="utf-8")
    return set(re.findall(r"codeClassName === 'language-(\w+)'", src))


def test_the_readme_lists_every_widget_the_frontend_renders():
    """The feature table said three block types. The renderer dispatches five,
    so two capabilities were invisible to anyone reading the README to decide
    whether this does what they need."""
    fences = _rendered_fences()
    assert fences, "no fence dispatch found — has MarkdownContent changed shape?"
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    row = next((ln for ln in readme.splitlines() if "**Widgets**" in ln), "")
    assert row, "the README no longer has a Widgets row"
    missing = sorted(f for f in fences if f"```{f}" not in row)
    assert not missing, f"the frontend renders {missing} but the README does not mention them"


def test_the_backend_integrity_check_covers_every_rendered_fence():
    """A block the frontend renders but the backend's splice does not know
    about arrives mangled whenever a model echoes it back imperfectly."""
    mcp = (_ROOT / "src" / "runspace" / "workspace" / "backend" / "_mcp_ui.py").read_text(
        encoding="utf-8"
    )
    covered = set(re.findall(r"\(([a-z|]+)\)", mcp))
    covered = {f for group in covered for f in group.split("|")}
    # mermaid is rendered but is not a JSON payload, so it has nothing to splice
    unguarded = sorted(f for f in _rendered_fences() - {"mermaid"} if f not in covered)
    assert not unguarded, f"rendered but outside the integrity check: {unguarded}"
