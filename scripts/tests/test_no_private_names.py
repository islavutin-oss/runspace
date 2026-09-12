"""Nothing in this repository may name private work.

This package is published to PyPI and written on machines that also hold
private client projects. Twice a comment carried something across that line: a
client's tool name used as an example, and a group chat's real id used to
illustrate what a title replaces. Neither was a credential — which is the
point. A reviewer looking for secrets does not catch a name.

The names themselves are not in this repository, and must not be: a blocklist
that spells out what it hides publishes it. They live in `.private-names`,
git-ignored. These tests exercise the mechanism with a list of their own, so
they are meaningful on a clone that has no such file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from check_private_names import _tracked, name_pattern, scan  # noqa: E402


def test_no_tracked_file_names_private_work():
    """The real check, when a list is configured. Without one there is nothing
    to compare against, and a clone without the file is the normal case."""
    if name_pattern() is None:
        pytest.skip("no .private-names configured — generic rules only")
    findings = scan(_tracked())
    assert not findings, "private work named in a public package:\n  " + "\n  ".join(findings)


def test_the_check_catches_a_name(tmp_path):
    """A guard nobody has seen fail is a guard nobody should trust."""
    leak = tmp_path / "leak.py"
    leak.write_text("# tool name -> label, e.g. `widget_count`\n")
    findings = scan([str(leak)], names=re.compile("widget_count", re.I))
    assert len(findings) == 1 and "widget_count" in findings[0], findings


def test_the_check_catches_a_real_chat_id(tmp_path):
    leak = tmp_path / "leak.py"
    # Built rather than written: a literal here would be flagged by the very
    # check this test exercises, which is the correct behaviour.
    real_shaped = "-100" + "9876543210"
    leak.write_text(f"CHAT = '{real_shaped}'\n")
    findings = scan([str(leak)], names=None)
    assert len(findings) == 1 and "chat id" in findings[0], findings


def test_the_documented_placeholder_is_allowed(tmp_path):
    ok = tmp_path / "doc.md"
    ok.write_text("chat_id: -100" + "1234567890\n")
    assert scan([str(ok)], names=None) == []


def test_the_name_list_is_not_tracked():
    """The failure this file is second-guessing: the first version of the
    checker carried the names inline, in a public file."""
    assert ".private-names" not in _tracked()
    assert (ROOT / ".private-names.example").is_file(), "the example is what a clone starts from"


def test_the_hook_runs_the_same_check():
    hook = ROOT / ".githooks" / "pre-commit"
    assert hook.is_file(), "the hook is gone; git config core.hooksPath .githooks"
    text = hook.read_text(encoding="utf-8")
    assert "check_private_names.py" in text
    assert "--staged" in text, "the hook must judge the staged blob, not the worktree"
