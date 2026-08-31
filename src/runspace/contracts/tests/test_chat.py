"""Pin the wire shapes that any runtime must produce/consume."""

from __future__ import annotations

import pytest

from runspace.contracts import (
    AttachmentInput,
    ChatRequest,
    ChatResponse,
    RoutineCreateRequest,
    RoutineDelivery,
)


def test_chat_request_resolves_app_id_alias():
    """ChatRequest accepts agent_id as a back-compat alias for app_id."""
    r = ChatRequest(agent_id="ada", message="hi")
    assert r.resolved_app_id == "ada"
    r = ChatRequest(app_id="nova", agent_id="ignored")
    # app_id wins when both supplied
    assert r.resolved_app_id == "nova"
    r = ChatRequest()
    assert r.resolved_app_id == ""


def test_chat_request_default_collections_are_empty():
    r = ChatRequest()
    assert r.file_ids == []
    assert r.attachments == []
    assert r.media_base64 is None


def test_chat_response_serializes_minimal():
    r = ChatResponse(app_id="ada", app_name="Ada", response="ok", session_id="s1")
    d = r.model_dump()
    assert d["tools_used"] == []
    assert d["attachments"] == []


def test_routine_delivery_modes():
    """Three valid kinds — channel, dm, silent."""
    for kind in ("channel", "dm", "silent"):
        rd = RoutineDelivery(kind=kind, target="x")
        assert rd.kind == kind


def test_routine_delivery_rejects_unknown_kind():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RoutineDelivery(kind="email")  # type: ignore[arg-type]


def test_routine_create_requires_delivery():
    """delivery is required — caller has to pick a destination."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RoutineCreateRequest(  # type: ignore[call-arg]
            agent_id="luca",
            schedule="0 9 * * *",
            prompt="hi",
        )


def test_attachment_input_default_content_empty():
    a = AttachmentInput(name="a.pdf", type="application/pdf", size=12)
    assert a.content == ""


def test_legacy_models_module_still_works():
    """Back-compat shim — `workspace.backend.models` re-exports contracts."""
    from runspace.contracts import ChatRequest as New
    from runspace.workspace.backend.models import ChatRequest as Old

    assert Old is New
