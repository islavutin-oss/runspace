"""Every console script pyproject declares must resolve in the shipped package.

A dangling entry point is invisible in the repository — the module it names
still exists in the working tree, or nobody runs the installed command — and
becomes a ModuleNotFoundError on the first thing a new user types.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # tomllib is 3.11+; the project supports 3.10
    import tomli as tomllib

_ROOT = Path(__file__).resolve().parents[4].parent  # …/src/runspace/workspace/cli/tests → repo root


def _declared_scripts() -> dict[str, str]:
    pyproject = _ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("scripts", {})


def test_pyproject_declares_at_least_one_script():
    """Guards the test itself: an empty dict would make the check below vacuous."""
    assert _declared_scripts(), "no [project.scripts] found — has the path moved?"


@pytest.mark.parametrize("name,target", sorted(_declared_scripts().items()))
def test_each_console_script_target_is_importable_and_callable(name, target):
    module_path, _, attr = target.partition(":")
    assert attr, f"{name} = {target!r} names no attribute"

    module = import_module(module_path)
    assert callable(getattr(module, attr)), f"{target} is not callable"


@pytest.mark.parametrize("name,target", sorted(_declared_scripts().items()))
def test_each_console_script_ships_inside_the_packaged_tree(name, target):
    """A target outside `src/runspace` is not in the wheel, so it installs broken
    even though it imports fine from a checkout."""
    module_path = target.partition(":")[0]
    assert module_path.split(".")[0] == "runspace", (
        f"{name} points at {module_path!r}, which is outside the packaged "
        "`runspace` tree and will not be present after pip install"
    )
