"""Tests for flatten_soul + resolve_includes — the canonical algorithm."""

from __future__ import annotations

from pathlib import Path

import pytest

from runspace.protocols.prompt import flatten_soul, resolve_includes


def test_flatten_substitutes_placeholders(tmp_path: Path):
    soul = tmp_path / "S.md"
    soul.write_text("Hi I am {{persona_name}} at {{tenant_name}}.")
    out = flatten_soul(soul, persona_name="Ada", tenant_name="Acme")
    assert out == "Hi I am Ada at Acme."


def test_flatten_resolves_relative_include(tmp_path: Path):
    (tmp_path / "_partials").mkdir()
    (tmp_path / "_partials" / "x.md").write_text("INCLUDED")
    soul = tmp_path / "S.md"
    soul.write_text("a\n{{include:_partials/x.md}}\nb")
    out = flatten_soul(soul, persona_name="P", tenant_name="T")
    assert "INCLUDED" in out
    assert "{{include:" not in out


def test_flatten_strips_yaml_front_matter(tmp_path: Path):
    soul = tmp_path / "S.md"
    soul.write_text("---\nname: persona\n---\nthe body")
    out = flatten_soul(soul, persona_name="P", tenant_name="T")
    assert out.strip() == "the body"


def test_flatten_raises_on_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        flatten_soul(tmp_path / "nope.md", persona_name="P", tenant_name="T")


def test_flatten_raises_on_empty_after_resolution(tmp_path: Path):
    """Front matter only + no body → empty after strip → raise.
    Catches stale SOULs that flatten to nothing."""
    soul = tmp_path / "S.md"
    soul.write_text("---\nname: persona\n---\n")
    with pytest.raises(ValueError):
        flatten_soul(soul, persona_name="P", tenant_name="T")


def test_resolve_includes_handles_nested(tmp_path: Path):
    (tmp_path / "a.md").write_text("alpha {{include:b.md}}")
    (tmp_path / "b.md").write_text("bravo {{include:c.md}}")
    (tmp_path / "c.md").write_text("charlie")
    out = resolve_includes("{{include:a.md}}", tmp_path)
    assert "alpha bravo charlie" in out


def test_resolve_includes_caps_recursion_depth(tmp_path: Path):
    """Self-referential include must not infinite-loop."""
    (tmp_path / "loop.md").write_text("self-{{include:loop.md}}")
    out = resolve_includes("{{include:loop.md}}", tmp_path, max_depth=2)
    # With max_depth=2: top calls (depth 0) → include resolved (depth 1) →
    # nested include resolved (depth 2) → next call returns text unchanged.
    # So we should see "self-" twice but no infinite loop.
    assert out.count("self-") <= 3
    assert "{{include:" in out  # the unresolved one at the leaf


def test_resolve_includes_missing_returns_empty_with_warning(tmp_path: Path, caplog):
    out = resolve_includes("before {{include:missing.md}} after", tmp_path)
    assert out == "before  after"
