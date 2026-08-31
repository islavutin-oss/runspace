"""Transcriber Protocol — pin the structural contract."""

from __future__ import annotations

import pytest

from runspace.protocols.transcriber import Transcriber, TranscriptionResult


def test_transcription_result_is_dataclass():
    r = TranscriptionResult(text="hello", model="whisper-large-v3", language="en", duration_ms=120)
    assert r.text == "hello"
    assert r.language == "en"


def test_minimal_async_transcriber_satisfies_protocol():
    class _T:
        async def transcribe(
            self, audio: bytes, mime: str = "audio/ogg", language: str | None = None
        ) -> TranscriptionResult:
            return TranscriptionResult(text="x")

    assert isinstance(_T(), Transcriber)


def test_object_without_transcribe_fails_isinstance():
    class _NotATranscriber:
        async def something_else(self): ...

    assert not isinstance(_NotATranscriber(), Transcriber)


def test_agentino_audio_transcriber_satisfies_contract():
    """When the agentino framework is importable, its AudioTranscriber
    must satisfy the Transcriber Protocol structurally — that's the
    load-bearing claim that lets the workspace gateway type at the
    Protocol and still wire agentino's concrete impl unchanged."""
    try:
        from agentino.extras.audio import AudioTranscriber
    except Exception:
        pytest.skip("agentino not importable in this environment")

    t = AudioTranscriber(base_url="https://example/v1", api_key="k", model="whisper")
    assert isinstance(t, Transcriber), (
        "agentino.extras.audio.AudioTranscriber no longer satisfies "
        "Transcriber. Either the Protocol drifted (rename of "
        "transcribe?) or AudioTranscriber lost the method. Fix one side."
    )


def test_no_agentino_import_in_protocol_module():
    """The Protocol module must remain runtime-free."""
    from runspace.protocols import transcriber as m

    src = open(m.__file__).read()
    for forbidden in ("import agentino", "from agentino"):
        assert forbidden not in src, (
            f"protocols/transcriber.py imports `{forbidden}` — that "
            f"re-couples the contract layer to the agentino runtime."
        )
