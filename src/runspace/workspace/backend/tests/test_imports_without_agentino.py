"""Importing runspace must not pull a runtime in."""

from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path(__file__).resolve().parents[3]


def _module_level_runtime_imports(path: Path) -> list[str]:
    """Imports of an agent runtime evaluated at import time.

    Lazy imports inside a function are the supported pattern — they cost
    nothing until the feature is used — so only module scope counts.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in tree.body:  # module scope only
        targets = []
        if isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets = [node.module or ""]
        elif isinstance(node, ast.Try):
            # `try: import x except ImportError:` is still module scope.
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    targets += [a.name for a in sub.names]
                elif isinstance(sub, ast.ImportFrom):
                    targets.append(sub.module or "")
        for t in targets:
            if t == "agentino" or t.startswith("agentino."):
                found.append(f"{path.name}:{node.lineno} {t}")
    return found


# Two helpers define agentino tools with the `@tool` decorator, which has to
# run at definition time — there is no lazy way to apply a decorator. They are
# agentino-specific by nature and are never imported by the gateway, which the
# next test checks rather than assumes. Anything else appearing here is a
# regression: it would turn the optional extra into a required one.
_ALLOWED = {
    "src/runspace/helpers/documents/branded_pdf.py",
    "src/runspace/helpers/documents/article_reader.py",
}


def test_no_module_imports_a_runtime_at_import_time():
    offenders: list[str] = []
    for f in _PKG.rglob("*.py"):
        rel = f.relative_to(_PKG.parents[1]).as_posix()
        if "/tests/" in f.as_posix() or f.name.startswith("test_") or rel in _ALLOWED:
            continue
        offenders += _module_level_runtime_imports(f)
    assert not offenders, (
        "these import an agent runtime at module scope, which makes the "
        "optional extra required: " + ", ".join(offenders)
    )


def test_the_allowlist_does_not_outlive_the_files_it_names():
    """An allowlist entry for a deleted or renamed file silently stops
    protecting anything."""
    for rel in _ALLOWED:
        assert (_PKG.parents[1] / rel).exists(), f"{rel} is allowlisted but does not exist"


def test_the_gateway_imports_with_the_runtime_absent():
    """The property the documentation claims, checked in a clean interpreter
    with agentino made unimportable.

    Not "agentino is never in sys.modules": when it *is* installed,
    `protocols.__init__` deliberately registers runspace's file storage and
    branded PDF renderer with agentino's standard tools. That integration is
    wanted. What must hold is that its absence is survivable — otherwise the
    optional extra is required and the README is wrong.
    """
    import subprocess
    import sys

    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'agentino' or name.startswith('agentino.'):\n"
        "            raise ModuleNotFoundError(name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "import runspace\n"
        "from runspace.workspace.backend import WorkspaceGateway, AppRegistry\n"
        "from runspace.protocols import get_store, get_file_storage\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(_PKG.parents[1])
    )
    assert out.returncode == 0 and out.stdout.strip() == "ok", (
        "runspace failed to import with agentino unavailable — the [agentino] "
        "extra is required in practice:\n" + out.stderr[-800:]
    )
