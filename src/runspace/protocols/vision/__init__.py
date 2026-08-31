"""Vision adapter — see ADR-0001."""

from .protocol import Vision

try:
    from .codex_vision import CodexVision
except ImportError:  # pragma: no cover
    CodexVision = None  # type: ignore[assignment]

try:
    from .fixture_vision import FixtureVision
except ImportError:  # pragma: no cover
    FixtureVision = None  # type: ignore[assignment]

__all__ = ["Vision", "CodexVision", "FixtureVision"]
