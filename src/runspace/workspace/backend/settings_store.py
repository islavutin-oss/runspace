"""Persisted values behind the settings screen."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def defaults_from_schema(sections: list[dict]) -> dict[str, Any]:
    """The values a workspace should show before anyone has saved anything.

    Each section type keeps its values in a different place — `field` for the
    single-value types, `fields[]` for pairs, `items[]` for toggles — so the
    shapes are read rather than guessed.
    """
    out: dict[str, Any] = {}
    for s in sections or []:
        kind = s.get("type")
        if kind in ("text", "schedule"):
            key = s.get("field") or s.get("id")
            if key:
                out[key] = s.get("default", "")
        elif kind == "number_pair":
            for f in s.get("fields") or []:
                if f.get("key"):
                    out[f["key"]] = f.get("default", 0)
        elif kind == "toggle_list":
            for i in s.get("items") or []:
                if i.get("key"):
                    out[i["key"]] = bool(i.get("value", i.get("default", False)))
        elif kind == "key_value":
            for f in s.get("fields") or []:
                if f.get("key"):
                    out[f["key"]] = f.get("value", f.get("default", ""))
    return out


class SettingsStore:
    """Values for one tenant, on disk."""

    def __init__(self, tenant_id: str = "default", *, root: str | Path = ".runspace/settings"):
        self.tenant_id = tenant_id or "default"
        self.path = Path(root) / f"{self.tenant_id}.json"

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            # A truncated file should not take the settings screen down; the
            # declared defaults are a safe floor to fall back to.
            return {}

    def load(self, sections: list[dict] | None = None) -> dict[str, Any]:
        """Saved values layered over the schema defaults.

        Defaults first so a section added after someone last saved still
        appears with its intended value instead of blank.
        """
        values = defaults_from_schema(sections or [])
        values.update(self._read())
        return values

    def save(self, values: dict[str, Any], sections: list[dict] | None = None) -> dict[str, Any]:
        """Merge and persist. Returns the full resulting value set.

        Merging rather than replacing means a client that PUTs one section
        does not silently wipe the others.
        """
        if not isinstance(values, dict):
            raise ValueError("settings must be a JSON object")
        with _LOCK:
            current = self._read()
            current.update(values)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
            # Atomic replace, so a crash mid-write cannot leave a half-file
            # that the next read has to recover from.
            tmp.replace(self.path)
        merged = defaults_from_schema(sections or [])
        merged.update(current)
        return merged
