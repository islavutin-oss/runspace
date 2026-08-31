"""`{{include:runspace:<name>}}` — partials that ship with the package."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from runspace.protocols.prompt.flatten import resolve_includes

_PKG = Path(__file__).resolve().parents[2]


def test_the_shipped_widget_partial_resolves(tmp_path):
    out = resolve_includes("{{include:runspace:widgets.md}}", tmp_path)
    assert "Answering with components" in out


def test_it_resolves_from_any_workspace_directory(tmp_path):
    """The point of the scheme: a workspace anywhere on disk gets the partial
    without copying it, so there is one copy to keep correct."""
    deep = tmp_path / "some" / "workspace" / "agents" / "analyst"
    deep.mkdir(parents=True)
    assert "Answering with components" in resolve_includes("{{include:runspace:widgets.md}}", deep)


def test_every_chart_type_the_renderer_accepts_is_documented():
    """The drift this scheme exists to prevent, asserted directly."""
    parser = _PKG.parent.parent / "workspace/frontend/shared/components/parseChartConfig.ts"
    assert parser.exists(), parser
    import re

    body = re.search(r"_NUMERIC_TYPES[^=]*=\s*\[([^\]]*)\]", parser.read_text(encoding="utf-8"))
    assert body, "could not find _NUMERIC_TYPES in parseChartConfig.ts"
    types = {t.strip().strip("'\"") for t in body.group(1).split(",") if t.strip()}
    assert len(types) >= 8, f"parsed too few types, regex is wrong: {types}"
    doc = (_PKG / "templates" / "widgets.md").read_text(encoding="utf-8")
    missing = {t for t in types if f"`{t}`" not in doc}
    assert not missing, f"renderer accepts {sorted(missing)} but the partial never mentions them"


def test_every_fence_the_renderer_dispatches_is_documented():
    md = _PKG.parent.parent / "workspace/frontend/shared/components/MarkdownContent.tsx"
    assert md.exists(), md
    import re

    fences = set(re.findall(r"language-(\w+)'", md.read_text(encoding="utf-8")))
    doc = (_PKG / "templates" / "widgets.md").read_text(encoding="utf-8")
    missing = {f for f in fences if f"`{f}`" not in doc}
    assert not missing, f"renderer dispatches {sorted(missing)} but the partial never mentions them"


@pytest.mark.parametrize(
    "attack",
    [
        "runspace:../../../etc/passwd",
        "runspace:../protocols/prompt/flatten.py",
        "runspace:/etc/passwd",
    ],
)
def test_traversal_out_of_the_template_directory_is_refused(attack, tmp_path):
    """An include path is workspace-authored. A workspace that can name any
    path on disk can read a private key into a prompt and mail it to a model."""
    out = resolve_includes("{{include:" + attack + "}}", tmp_path)
    assert out == "", f"{attack} was not refused"
    assert "root:" not in out and "PRIVATE KEY" not in out


def test_the_partial_ships_in_the_wheel():
    """It resolves from the source tree either way; the question is whether
    `pip install runspace` gets it. A root-level templates/ directory would
    not have — `packages = ["src/runspace"]` only ships the package."""
    wheels = sorted((_PKG.parent.parent / "dist").glob("runspace-*.whl"))
    if not wheels:
        pytest.skip("no wheel built")
    names = zipfile.ZipFile(wheels[-1]).namelist()
    assert any(n.endswith("runspace/templates/widgets.md") for n in names), (
        f"widgets.md is not in {wheels[-1].name}; templates/ is outside the shipped package"
    )
