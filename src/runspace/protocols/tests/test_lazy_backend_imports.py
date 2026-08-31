"""Pin: protocols.store and protocols.embeddings load even when their optional backend SDKs (`supabase`, `openai`) aren't installed."""

from __future__ import annotations

import importlib
import sys


def _reimport(module_name: str, blocked: set[str]):
    """Drop module_name (and submodules) from sys.modules, then re-import
    while pretending the modules in `blocked` aren't installed.

    Yields the freshly imported module. After the test, the ORIGINAL
    sys.modules state is restored byte-for-byte — including the
    original class objects under module_name. This matters because
    other tests in the suite (e.g. test_registry.py) hold references
    to classes loaded at collection time; if we drop and re-import
    here without restoring, those references become stale and
    `isinstance` checks against them fail in unrelated tests.
    Pre-2026-05-07 this function dropped the target package twice
    (once before the test, once in the finally) which broke class
    identity for everything that ran after — the canonical example
    was protocols/tests/test_registry.py mysteriously failing only
    when test_lazy_backend_imports.py ran before it.
    """
    # Snapshot everything we're about to mutate so we can restore exactly.
    saved_target: dict[str, object] = {}
    for n in list(sys.modules):
        if n == module_name or n.startswith(module_name + "."):
            saved_target[n] = sys.modules.pop(n)

    saved_blocked: dict[str, object | None] = {}
    for blocked_mod in blocked:
        for n in list(sys.modules):
            if n == blocked_mod or n.startswith(blocked_mod + "."):
                saved_blocked[n] = sys.modules.pop(n)
        # Setting to None makes future `import` raise ModuleNotFoundError.
        sys.modules[blocked_mod] = None  # type: ignore[assignment]

    try:
        return importlib.import_module(module_name)
    finally:
        # Drop whatever the re-import + the None sentinel left behind.
        for blocked_mod in blocked:
            sys.modules.pop(blocked_mod, None)
        for n in list(sys.modules):
            if n == module_name or n.startswith(module_name + "."):
                del sys.modules[n]
        # Restore the originals — class objects keep their identity,
        # subsequent tests see exactly what they saw before.
        for n, m in saved_blocked.items():
            sys.modules[n] = m  # type: ignore[assignment]
        for n, m in saved_target.items():
            sys.modules[n] = m
        # sys.modules is not the only place a submodule is recorded:
        # importlib also binds it as an attribute of its parent package, and
        for n, m in saved_target.items():
            parent_name, _, child = n.rpartition(".")
            parent = sys.modules.get(parent_name) if parent_name else None
            if parent is not None:
                setattr(parent, child, m)


def test_protocols_store_imports_without_supabase():
    """protocols.store should load even when the `supabase` SDK is absent.

    Note on the design: supabase_store.py lazy-imports `supabase` inside
    its methods (not at module top), so the SupabaseStore *class* is
    importable even with the SDK missing. Calling its methods will fail
    later, which is what we want — the protocol layer loads, callers
    that don't actually use Supabase aren't blocked.

    The try/except wrapper in protocols/store/__init__.py is defensive
    against a future refactor that hoists `import supabase` to the
    module top. If that happens, this test still passes (the wrapper
    catches and sets SupabaseStore=None) AND
    `test_supabase_methods_fail_loud_without_sdk` will detect the
    method-level breakage.
    """
    mod = _reimport("runspace.protocols.store", blocked={"supabase"})
    # Core surface still there
    assert mod.Store is not None
    assert mod.FileStore is not None
    assert mod.InMemoryStore is not None
    # SupabaseStore class is importable (lazy-imports supabase inside
    # methods, not at top). May be None only if a future refactor
    # makes the import eager AND breaks — in that case the wrapper saves us.
    # Either outcome is acceptable; the inverse (top-level import +
    # uncaught ImportError) would crash protocols.store loading.


def test_protocols_embeddings_imports_without_openai():
    """protocols.embeddings should load even without the `openai` SDK.
    OpenAICompatEmbeddings becomes None; FixtureEmbeddings still works."""
    mod = _reimport("runspace.protocols.embeddings", blocked={"openai"})
    assert mod.Embeddings is not None
    assert mod.FixtureEmbeddings is not None
    assert mod.OpenAICompatEmbeddings is None, (
        "OpenAICompatEmbeddings was imported despite openai being unavailable. "
        "Check the try/except wrapper in protocols/embeddings/__init__.py."
    )


def test_protocols_top_level_imports_without_either():
    """The whole `protocols` package should load with both backends absent.
    This is the sandbox/CI-with-fixtures scenario: only Fixture* + InMemory*
    + FileStore are needed; Supabase + OpenAI shouldn't gate basic import."""
    mod = _reimport("runspace.protocols", blocked={"supabase", "openai"})
    # Public surface from registry — these don't need backend SDKs at import
    assert mod.get_store is not None
    assert mod.get_vision is not None
    assert mod.get_embeddings is not None
    assert mod.Store is not None
    assert mod.Embeddings is not None


def test_supabase_store_module_still_importable_directly_when_supabase_present():
    """Negative-control: when supabase IS available (CI default), the
    SupabaseStore class IS importable. Confirms the lazy block doesn't
    silently disable the production path."""
    # Skip if the SDK genuinely isn't installed in this env (sandbox etc).
    try:
        import supabase  # noqa: F401
    except ImportError:
        import pytest

        pytest.skip("supabase not installed in this environment — negative control N/A")

    # Re-import with NO blocks
    mod = _reimport("runspace.protocols.store", blocked=set())
    assert mod.SupabaseStore is not None
    # Sanity: it's an actual class, not the sentinel None
    assert isinstance(mod.SupabaseStore, type)
