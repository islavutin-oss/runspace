"""FileStore — sandbox/local Store backend."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .protocol import Store


class FileStore(Store):
    """Atomic JSON-file-per-collection Store implementation."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ── helpers ─────────────────────────────────────────────────────────
    def _path(self, collection: str) -> Path:
        # Refuse path-traversal: collection name must not contain path
        # separators, parent-dir markers, or null bytes. Without this,
        # `store.save("../../../etc/sneaky", ...)` would escape `root`.
        if (
            not collection
            or "/" in collection
            or "\\" in collection
            or "\x00" in collection
            or collection in ("..", ".")
            or collection.startswith(".")
        ):
            raise ValueError(
                f"invalid collection name {collection!r} — must not "
                "contain path separators, null bytes, or start with '.'"
            )
        out = self.root / f"{collection}.json"
        # Defense in depth: even after the syntactic check, ensure the
        # resolved path is still under root.
        try:
            out.resolve().relative_to(self.root.resolve())
        except ValueError:
            raise ValueError(f"collection {collection!r} resolves outside store root")
        return out

    def _lock(self, collection: str) -> threading.Lock:
        # Lazy per-collection lock so concurrent writes to different
        # collections don't serialize.
        with self._locks_guard:
            if collection not in self._locks:
                self._locks[collection] = threading.Lock()
            return self._locks[collection]

    def _read(self, collection: str) -> list[dict]:
        p = self._path(collection)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write(self, collection: str, records: list[dict]) -> None:
        p = self._path(collection)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    # ── Store ───────────────────────────────────────────────────────────
    def list(self, collection: str) -> list[dict]:
        return self._read(collection)

    def get(self, collection: str, id: str) -> dict | None:
        for r in self._read(collection):
            if r.get("id") == id:
                return r
        return None

    def save(self, collection: str, record: dict) -> dict:
        if "id" not in record:
            raise ValueError("record must include an 'id' field")
        with self._lock(collection):
            records = self._read(collection)
            for i, r in enumerate(records):
                if r.get("id") == record["id"]:
                    records[i] = record
                    break
            else:
                records.append(record)
            self._write(collection, records)
            return record

    def update(self, collection: str, id: str, **fields) -> dict | None:
        with self._lock(collection):
            records = self._read(collection)
            for i, r in enumerate(records):
                if r.get("id") == id:
                    records[i] = {**r, **fields}
                    self._write(collection, records)
                    return records[i]
        return None

    def delete(self, collection: str, id: str) -> bool:
        with self._lock(collection):
            records = self._read(collection)
            new = [r for r in records if r.get("id") != id]
            if len(new) == len(records):
                return False
            self._write(collection, new)
            return True

    def query(self, collection: str, **predicate) -> list[dict]:
        if not predicate:
            return self._read(collection)
        return [
            r for r in self._read(collection) if all(r.get(k) == v for k, v in predicate.items())
        ]
