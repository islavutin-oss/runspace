"""A bot token must not reach a log."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx
import pytest

from runspace.ingestion._redact import redact

_TOKEN = "123456789:AAH-thisIsTheSecretPart_x"
# <repo>/src/runspace — parents[2] is .../workspace, which has no
# ingestion/ directory, so the scan below found nothing and passed.
_SRC = Path(__file__).resolve().parents[3]


def _status_error() -> httpx.HTTPStatusError:
    url = f"https://api.telegram.org/bot{_TOKEN}/getUpdates"
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        f"Client error '401 Unauthorized' for url '{url}'",
        request=req,
        response=httpx.Response(401, request=req),
    )


def test_the_unredacted_error_really_does_carry_the_token():
    """Guards the test below: if httpx stops including the URL, these tests
    would pass while checking nothing."""
    assert "thisIsTheSecretPart" in str(_status_error())


def test_redact_removes_the_secret_and_keeps_the_bot_id():
    out = redact(_status_error())
    assert "thisIsTheSecretPart" not in out
    assert "123456789" in out, "the bot id identifies which bot failed and is worth keeping"


@pytest.mark.parametrize(
    "text",
    [
        "bot123456789:AAH-secret_value-x/getUpdates",
        "https://api.telegram.org/file/bot987654:ZZZ-another_one/photo.jpg",
        "two bot111111:AAAAAAAAAAAA and bot222222:BBBBBBBBBBBB here",
    ],
)
def test_every_token_shape_is_caught(text):
    out = redact(text)
    assert not re.search(r"bot\d{5,}:[A-Za-z0-9_-]{10,}", out), f"a token survived: {out}"


def test_text_without_a_token_is_untouched():
    for s in ("Connection refused", "bot is offline", "robot:12345"):
        assert redact(s) == s


def test_no_telegram_log_call_formats_a_bare_exception():
    """The regression this exists for: a log line that interpolates the raw
    exception instead of the redacted one.

    Parsed rather than pattern-matched. Two regex versions of this check
    passed against a real regression — one only looked at the last argument,
    the other anchored to end-of-line and these calls span several lines.
    """
    import ast

    offenders = []
    checked = 0
    for rel in ("ingestion/polling.py", "ingestion/telegram.py"):
        f = _SRC / rel
        assert f.exists(), f"{f} not found — this check would scan nothing and pass"
        checked += 1
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)):
                continue
            if fn.value.id != "log" or fn.attr not in {
                "warning",
                "error",
                "exception",
                "info",
                "debug",
            }:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id == "e":
                    offenders.append(f"{rel}:{node.lineno} log.{fn.attr}(..., e, ...)")
    assert checked == 2, "not every source file was scanned"
    assert not offenders, "log an exception through redact():\n  " + "\n  ".join(offenders)


def test_a_real_log_record_comes_out_clean(caplog):
    log = logging.getLogger("runspace.test.redaction")
    with caplog.at_level(logging.WARNING):
        log.warning("[tg-poll] loop error (%s): %s", "HTTPStatusError", redact(_status_error()))
    assert caplog.records
    assert "thisIsTheSecretPart" not in caplog.text
