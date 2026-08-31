"""Resolve the optional reply filter an app declares in ``workspace.yml``."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module

ResponseFilter = Callable[[str, int, int, bool], "str | None"]


def load_response_filter(spec: str | ResponseFilter | None) -> ResponseFilter | None:
    """Resolve *spec* to a callable, or None when no filter is declared.

    Accepts an already-callable object, or a ``"module.path:attribute"``
    string. Raises ValueError if the string is malformed and ImportError /
    AttributeError if it does not resolve.
    """
    if not spec:
        return None
    if callable(spec):
        return spec
    if not isinstance(spec, str) or ":" not in spec:
        raise ValueError(f"response_filter must be 'module.path:attribute', got {spec!r}")
    module_path, _, attr = spec.partition(":")
    target = getattr(import_module(module_path), attr)
    if not callable(target):
        raise TypeError(f"response_filter {spec!r} is not callable")
    return target
