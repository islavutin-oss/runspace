"""Keep bot tokens out of logs."""

from __future__ import annotations

import re

# `bot` followed by a Telegram token: digits, a colon, then the secret part.
_BOT_TOKEN = re.compile(r"(bot)(\d{5,})(:)([A-Za-z0-9_-]{10,})")


def redact(text: object) -> str:
    """Return `text` with any Telegram bot token replaced.

    The numeric bot id is kept — it identifies which bot failed, which is the
    useful half — and only the secret is removed.
    """
    return _BOT_TOKEN.sub(r"\1\2\3<redacted>", str(text))
