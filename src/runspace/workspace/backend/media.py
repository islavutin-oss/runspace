"""Audio transcription glue."""

from __future__ import annotations

import os as _os
import re as _re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from runspace.protocols.transcriber import Transcriber


def _resolve_env_vars(value: str) -> str:
    """Resolve ${VAR} and ${VAR:-default} references in config values."""

    def _replace(m):
        name = m.group(1)
        default = m.group(2) or ""
        return _os.environ.get(name, default)

    return (
        _re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", _replace, value) if isinstance(value, str) else value
    )


def _build_transcriber(audio_config: dict | None) -> Transcriber | None:
    """Build a Transcriber from workspace.yml audio section.

    Returns None when audio is unconfigured, explicitly disabled, or
    the agentino runtime (which provides the concrete impl) isn't
    importable in this process. Callers must handle None.
    """
    if not audio_config:
        return None
    if audio_config.get("enabled") is False:
        return None
    base_url = _resolve_env_vars(audio_config.get("base_url", ""))
    api_key = _resolve_env_vars(audio_config.get("api_key", ""))
    model = audio_config.get("model")
    if not base_url and not api_key:
        return None
    try:
        # Lazy: workspace can boot without agentino on the import path,
        # then the host (acme, globex, ...) is responsible for
        # injecting a Transcriber if it wants STT.
        from agentino.extras.audio import AudioTranscriber
    except ImportError:
        return None
    return AudioTranscriber(base_url=base_url or None, api_key=api_key or None, model=model)
