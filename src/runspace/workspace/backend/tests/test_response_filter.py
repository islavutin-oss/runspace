"""The reply-filter seam: runspace resolves what the app declares, nothing more."""

from __future__ import annotations

import pytest

from runspace.workspace.backend.response_filter import load_response_filter


def _accepts_everything(text, turn, max_turns, had_tool_calls):
    return None


def test_no_spec_means_no_filter():
    assert load_response_filter(None) is None
    assert load_response_filter("") is None


def test_callable_is_passed_through():
    assert load_response_filter(_accepts_everything) is _accepts_everything


def test_dotted_path_resolves():
    spec = f"{__name__}:_accepts_everything"
    assert load_response_filter(spec) is _accepts_everything


def test_missing_separator_is_rejected():
    with pytest.raises(ValueError, match="module.path:attribute"):
        load_response_filter("myapp.filters")


def test_unresolvable_module_raises():
    with pytest.raises(ImportError):
        load_response_filter("no_such_module_xyz:f")


def test_unresolvable_attribute_raises():
    with pytest.raises(AttributeError):
        load_response_filter(f"{__name__}:nope")


def test_non_callable_target_is_rejected():
    with pytest.raises(TypeError, match="not callable"):
        load_response_filter(f"{__name__}:NOT_CALLABLE")


NOT_CALLABLE = "just a string"
