"""InMemoryStore — process-local Store backend."""

from __future__ import annotations

import threading
from copy import deepcopy

from .protocol import Store


class InMemoryStore(Store):
    """Dict-backed Store impl. Same semantics as FileStore minus the
    on-disk persistence."""

    def __init__(self):
        # collection_name -> list[dict]
        self._data: dict[str, list[dict]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, collection: str) -> threading.Lock:
        with self._locks_guard:
            if collection not in self._locks:
                self._locks[collection] = threading.Lock()
            return self._locks[collection]

    def list(self, collection: str) -> list[dict]:
        # Return a deep copy so callers mutating the result don't pollute storage
        return [deepcopy(r) for r in self._data.get(collection, [])]

    def get(self, collection: str, id: str) -> dict | None:
        for r in self._data.get(collection, []):
            if r.get("id") == id:
                return deepcopy(r)
        return None

    def save(self, collection: str, record: dict) -> dict:
        if "id" not in record:
            raise ValueError("record must include an 'id' field")
        with self._lock(collection):
            records = self._data.setdefault(collection, [])
            for i, r in enumerate(records):
                if r.get("id") == record["id"]:
                    records[i] = deepcopy(record)
                    return deepcopy(record)
            records.append(deepcopy(record))
            return deepcopy(record)

    def update(self, collection: str, id: str, **fields) -> dict | None:
        with self._lock(collection):
            records = self._data.get(collection, [])
            for i, r in enumerate(records):
                if r.get("id") == id:
                    records[i] = {**r, **fields}
                    return deepcopy(records[i])
        return None

    def delete(self, collection: str, id: str) -> bool:
        with self._lock(collection):
            records = self._data.get(collection, [])
            for i, r in enumerate(records):
                if r.get("id") == id:
                    del records[i]
                    return True
        return False

    def query(self, collection: str, **predicate) -> list[dict]:
        records = self._data.get(collection, [])
        if not predicate:
            return [deepcopy(r) for r in records]
        return [deepcopy(r) for r in records if all(r.get(k) == v for k, v in predicate.items())]
