"""Code samples in the documentation have to be the language they claim."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[4].parent

# Match the opening fence's length and require the same length to close it.
# A shorter pattern stops at the first ``` inside the body, which is how a
# valid four-backtick block looks broken to a naive checker.
_FENCE = re.compile(r"^(`{3,})(python|yaml|yml|json)[ \t]*\r?\n(.*?)^\1[ \t]*$", re.S | re.M)


def _markdown() -> list[Path]:
    return sorted(p for p in _ROOT.rglob("*.md") if ".git" not in p.parts)


def _samples() -> list[tuple[str, str, str]]:
    out = []
    for f in _markdown():
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            continue
        for m in _FENCE.finditer(text):
            out.append((str(f.relative_to(_ROOT)), m.group(2), m.group(3)))
    return out


def test_samples_were_found():
    """Guards the parametrised test from passing because the fence pattern broke."""
    assert _samples(), "no fenced samples parsed — has the doc format changed?"


@pytest.mark.parametrize(
    "page,lang,body", _samples(), ids=lambda v: str(v)[:36] if isinstance(v, str) else ""
)
def test_a_sample_parses_as_the_language_it_claims(page, lang, body):
    if lang == "python":
        try:
            ast.parse(body)
        except SyntaxError as e:
            # `await` at module level is a documentation idiom, not a mistake
            if "await" in body and "outside function" in str(e):
                pytest.skip("module-level await, a documentation idiom")
            raise AssertionError(f"{page}: {e.msg} on line {e.lineno}") from None
    elif lang in ("yaml", "yml"):
        try:
            yaml.safe_load(body)
        except Exception as e:
            raise AssertionError(f"{page}: {e}") from None
    else:
        try:
            json.loads(body)
        except Exception as e:
            raise AssertionError(f"{page}: {e}") from None
