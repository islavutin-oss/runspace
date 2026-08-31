"""Prompt-engineering contract — runtime-agnostic."""

from .envelope import build_message_envelope  # noqa: F401
from .flatten import flatten_soul, resolve_includes  # noqa: F401

__all__ = ["flatten_soul", "resolve_includes", "build_message_envelope"]
