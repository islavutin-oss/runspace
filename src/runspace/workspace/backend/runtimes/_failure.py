"""What a caller is told when a runtime fails.

Every adapter used to write its own failure text, and each one named itself:
`[claude_code] timed out after 240s`, `[codex] binary not found: ...`. That
string is not a log line — it is delivered into the channel as the agent's
reply, so a visitor to a public workspace was shown which CLI sits behind the
agent, and in the no-reply case the runtime's raw stderr as well, which carries
absolute paths, model identifiers and anything the process chose to print.

An operator needs all of that; a caller needs none of it. The detail goes to
the log, the caller gets a sentence, and which runtime produced it is not part
of the sentence.

`RUNSPACE_FAILURE_TEXT` overrides the wording for deployments whose voice these
defaults do not match — the same seam every other piece of user-facing wording
has, rather than a branch per tenant in here.
"""

from __future__ import annotations

import os

_DEFAULT = "I could not finish that one. Please try again."

_TEXT = {
    "timeout": "That took longer than I'm allowed and I stopped. "
    "Try again, or ask for a narrower slice of it.",
    "empty": "Something went wrong on my side and I have nothing to show for it. Try again.",
    "unavailable": "I can't answer right now.",
}


def failure_text(kind: str = "") -> str:
    """A caller-safe sentence for a runtime failure.

    Names nothing about how the agent is run. Takes no detail argument on
    purpose: a signature that accepts one is a signature that will eventually
    be handed stderr.
    """
    override = os.environ.get("RUNSPACE_FAILURE_TEXT")
    if override:
        return override
    return _TEXT.get(kind, _DEFAULT)
