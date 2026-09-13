"""A runtime failure must not describe the runtime.

Adapters used to write their own failure text and each named itself — a string
like `[<adapter>] timed out after 240s` was not a log line, it was delivered
into the channel as the agent's reply. On a public workspace that tells every
visitor which CLI is behind the agent. The no-reply branch was worse: it
appended the process's raw stderr, which carries absolute paths, model
identifiers, and anything else the binary chose to print.

These tests are written against the shipped source of every adapter, not just
the one that had the bug, because the next adapter added beside them is the one
most likely to reintroduce it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from runspace.workspace.backend.runtimes._failure import failure_text

RUNTIMES = Path(__file__).resolve().parents[1] / "runtimes"
ADAPTERS = ["claude_code", "codex", "pi", "openclaw"]


@pytest.mark.parametrize("kind", ["timeout", "empty", "unavailable", "", "nonsense"])
def test_no_failure_text_names_a_runtime(kind):
    text = failure_text(kind).lower()
    for adapter in ADAPTERS + ["claude", "codex", "openclaw", "cli", "stderr"]:
        assert adapter not in text, f"{kind!r} names {adapter!r}: {text!r}"


@pytest.mark.parametrize("kind", ["timeout", "empty", "unavailable", "nonsense"])
def test_every_kind_says_something(kind):
    """Including one nobody defined — a caller must never get an empty reply."""
    assert failure_text(kind).strip()


def test_a_deployment_can_supply_its_own_wording(monkeypatch):
    monkeypatch.setenv("RUNSPACE_FAILURE_TEXT", "Ask me again in a moment.")
    assert failure_text("timeout") == "Ask me again in a moment."


def test_failure_text_refuses_to_carry_detail():
    """The signature is the guard. One that accepts a detail argument is one
    that will eventually be handed stderr."""
    import inspect

    params = list(inspect.signature(failure_text).parameters)
    assert params == ["kind"], f"failure_text grew a parameter: {params}"


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_no_adapter_builds_its_own_reply_string(adapter):
    """`text = f"[<adapter>] ..."` is the shape of the original bug."""
    source = (RUNTIMES / f"{adapter}.py").read_text(encoding="utf-8")
    offenders = re.findall(r'text\s*=\s*f?"\[[^"]*"', source)
    assert not offenders, f"{adapter} writes its own failure text: {offenders}"


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_no_adapter_puts_stderr_in_a_reply(adapter):
    """stderr may be logged. It may not be assigned to `text`."""
    source = (RUNTIMES / f"{adapter}.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("text =") or stripped.startswith("text="):
            assert "stderr" not in stripped, f"{adapter}: {stripped}"
