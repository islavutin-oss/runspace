"""Declarative std-tool bundles (replaces the old blanket agentino.tools.std auto-load)."""

from __future__ import annotations

from runspace.workspace.backend.runtimes.agentino import (
    _STD_BUNDLES,
    _load_std_bundles,
    _std_catalog,
)


def test_std_catalog_discovers_tools():
    cat = _std_catalog()
    names = {getattr(t, "name", None) for t in cat}
    # Sanity: the std package exposes the document + web tools we bundle.
    assert "create_document" in names
    assert "create_pdf" in names


def test_no_bundles_loads_nothing():
    """An agent that declares no std_tools gets zero std tools — the core of
    Max's lean parity."""
    assert _load_std_bundles([]) == []


def test_documents_bundle_loads_export_tools():
    tools = _load_std_bundles(["documents"])
    names = {t.name for t in tools}
    assert {"create_csv", "create_pdf", "create_document", "read_file"} <= names
    # Documents bundle must NOT pull in web/weather/memory.
    assert "get_weather" not in names
    assert "browse_web" not in names
    assert "remember" not in names


def test_web_bundle_scoped():
    names = {t.name for t in _load_std_bundles(["web"])}
    assert "fetch_web_data" in names
    assert "create_document" not in names


def test_multiple_bundles_union():
    names = {t.name for t in _load_std_bundles(["documents", "weather"])}
    assert "create_pdf" in names
    assert "get_weather" in names


def test_all_bundle_loads_full_catalog():
    all_tools = _load_std_bundles(["all"])
    assert len(all_tools) == len(_std_catalog())


def test_unknown_bundle_ignored():
    # Unknown names are dropped (logged), not fatal.
    assert _load_std_bundles(["bogus-bundle"]) == []


def test_bundle_names_map_to_real_tools():
    """Every tool named in a bundle must exist in the std catalog — guards
    against typos silently yielding empty bundles."""
    catalog_names = {getattr(t, "name", None) for t in _std_catalog()}
    for bundle, tool_names in _STD_BUNDLES.items():
        missing = tool_names - catalog_names
        assert not missing, f"bundle {bundle!r} names not in std catalog: {missing}"
