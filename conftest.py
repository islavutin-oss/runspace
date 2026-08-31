"""Test-wide isolation for anything that writes to disk.

The library's zero-configuration defaults deliberately resolve to a
project-local directory so a fresh install runs with no environment set. That
is right for a user and wrong for a test suite: without this fixture every
test shares one store and one file-storage root, and state leaks between them.
"""

import sys
from pathlib import Path

import pytest

# One import root for the whole suite. Individual test modules used to insert
# their own sys.path entries computed from parents[N]; those indices silently
# became wrong when the packages moved under src/, and pointing at
# src/runspace made `protocols`, `workspace` and friends importable a second
# time as top-level names. Two copies of a module mean two lru_caches and two
# sets of globals, which is impossible to debug from a test failure.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _isolated_storage_roots(tmp_path, monkeypatch):
    """Point every storage root at this test's own tmp_path.

    A test that sets its own root still wins — its monkeypatch runs after
    this fixture.
    """
    monkeypatch.setenv("STORE_FILE_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path / "files"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    # The adapter factories are lru_cached, so without this the first test to
    # build one pins its root for the whole session and every later test reads
    # the wrong directory.
    from runspace.protocols import reset

    def _reset_all():
        reset()
        # agentino's std tools keep their own process-wide handle to the
        # file-storage facade, which our reset() does not reach.
        try:
            from agentino.tools.std._file_storage import _reset_for_tests
            from agentino.tools.std.storage import _reset_for_tests as _reset_store
        except ImportError:
            return
        _reset_for_tests()
        _reset_store()

    _reset_all()
    yield
    _reset_all()
