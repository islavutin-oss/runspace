"""Who the current turn is for.

The role travels in a ContextVar because it is ambient to a request and read
far from where it is set — model selection in a runtime adapter, write
permission in the gateway. Threading it through every signature between those
two points would touch code that has no interest in it.

It lives here, not in a runtime adapter, because the gateway reads it too and
must not import one adapter to do so: `app_registry` dispatches on `app.type`
and imports no adapter eagerly, which is what keeps runspace runtime-agnostic.
"""

from __future__ import annotations

import contextvars

# The caller's role for this turn, or None when the host does not distinguish
# callers. None means "no role", never "trusted": a gate keyed on roles has to
# decide what an absent role gets, and every one here treats it as the least
# privileged.
caller_role: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "caller_role", default=None
)
