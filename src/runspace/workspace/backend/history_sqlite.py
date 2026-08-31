"""Chat history that survives a restart."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .app_registry import ChatHistoryStore

_DDL = """
CREATE TABLE IF NOT EXISTS chat_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id  TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_history_session
    ON chat_history (tenant_id, session_id, id);
"""


class SqliteChatHistoryStore(ChatHistoryStore):
    """File-backed chat history, scoped by tenant.

    One file can hold every tenant's history because every query is filtered by
    `tenant_id`. That mirrors the messaging backend, and the isolation is
    likewise something the tests assert rather than assume.
    """

    def __init__(
        self,
        tenant_id: str = "default",
        *,
        db_path: str | Path = ".runspace/history.sqlite",
        max_messages: int = 20,
    ) -> None:
        super().__init__(max_messages=max_messages)
        self.tenant_id = tenant_id
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # A FastAPI worker touches this from several threads; SQLite objects
        # are not shareable across them by default.
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def get(self, session_id: str) -> list[dict]:
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT role, content FROM chat_history "
                "WHERE tenant_id = ? AND session_id = ? ORDER BY id DESC LIMIT ?",
                (self.tenant_id, session_id, self._max),
            ).fetchall()
        # Newest-first above so the LIMIT keeps the tail, not the head.
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def add(self, session_id: str, role: str, content: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO chat_history (tenant_id, session_id, role, content) "
                "VALUES (?, ?, ?, ?)",
                (self.tenant_id, session_id, role, str(content)),
            )
            con.commit()

    def clear(self, session_id: str) -> bool:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "DELETE FROM chat_history WHERE tenant_id = ? AND session_id = ?",
                (self.tenant_id, session_id),
            )
            con.commit()
            return cur.rowcount > 0

    def seed(self, session_id: str, messages: list[dict]) -> None:
        """Replace a session's history outright.

        A demo workspace needs a conversation a first-time visitor can read.
        Appending would duplicate it on every deploy, so this is idempotent by
        construction: the session is emptied and rewritten.
        """
        with self._lock, self._connect() as con:
            con.execute(
                "DELETE FROM chat_history WHERE tenant_id = ? AND session_id = ?",
                (self.tenant_id, session_id),
            )
            con.executemany(
                "INSERT INTO chat_history (tenant_id, session_id, role, content) "
                "VALUES (?, ?, ?, ?)",
                [(self.tenant_id, session_id, m["role"], str(m["content"])) for m in messages],
            )
            con.commit()

    def sessions(self) -> list[str]:
        with self._lock, self._connect() as con:
            return [
                r["session_id"]
                for r in con.execute(
                    "SELECT DISTINCT session_id FROM chat_history WHERE tenant_id = ? "
                    "ORDER BY session_id",
                    (self.tenant_id,),
                )
            ]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SqliteChatHistoryStore(tenant_id={self.tenant_id!r}, db_path={str(self.db_path)!r})"
        )


def history_store_from_env(tenant_id: str = "default") -> ChatHistoryStore:
    """Pick a history store the way the rest of runspace picks a backend.

    `CHAT_HISTORY_BACKEND=sqlite` opts in; anything else keeps the in-memory
    default, so no existing deployment changes behaviour by upgrading.
    """
    import os

    if os.getenv("CHAT_HISTORY_BACKEND", "").lower() == "sqlite":
        return SqliteChatHistoryStore(
            tenant_id,
            db_path=os.getenv("CHAT_HISTORY_DB", ".runspace/history.sqlite"),
        )
    return ChatHistoryStore()
