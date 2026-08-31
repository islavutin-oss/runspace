"""Shared Supabase data access."""

from __future__ import annotations

import re

from supabase import Client, create_client

# ── connection ───────────────────────────────────────────────────
_clients: dict[tuple[str, str], Client] = {}


def get_client(url: str, key: str) -> Client:
    """A cached supabase-py client, keyed on (url, key)."""
    if not url or not key:
        raise ValueError("Supabase url and key are both required")
    cache_key = (url, key)
    if cache_key not in _clients:
        _clients[cache_key] = create_client(url, key)
    return _clients[cache_key]


# ── SQLite → Postgres dialect translation ────────────────────────
# Matches both datetime('now','-N unit') and date('now','-N unit').
_DT_DELTA = re.compile(
    r"(datetime|date)\(\s*'now'\s*,\s*'([+-]?)(\d+)\s+(\w+)'\s*\)", re.IGNORECASE
)
_DT_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
_DATE_NOW = re.compile(r"date\(\s*'now'\s*\)", re.IGNORECASE)


def _delta(m: re.Match[str]) -> str:
    base = "now()" if m.group(1).lower() == "datetime" else "current_date"
    op = "-" if m.group(2) == "-" else "+"
    return f"{base} {op} interval '{m.group(3)} {m.group(4)}'"


def translate(sql: str) -> str:
    """Rewrite the SQLite-isms a migrated codebase still carries —
    date functions and the custom ``lower_unicode()`` (Postgres
    ``lower()`` is already Unicode-aware)."""
    sql = _DT_DELTA.sub(_delta, sql)
    sql = _DT_NOW.sub("now()", sql)
    sql = _DATE_NOW.sub("current_date", sql)
    return sql.replace("lower_unicode(", "lower(")


def _quote(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def inline_params(sql: str, params) -> str:
    """Substitute ``?`` placeholders with safely-quoted literals.

    Standard-conforming strings (Postgres default) mean ``''`` is the
    only escape needed for string literals.
    """
    out: list[str] = []
    it = iter(params)
    for ch in sql:
        if ch == "?":
            try:
                out.append(_quote(next(it)))
            except StopIteration:
                out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


# ── result rows ──────────────────────────────────────────────────
class Row(dict):
    """A result row supporting BOTH ``row['col']`` and positional
    ``row[0]`` — the dual access a sqlite3.Row codebase relies on."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _Result:
    """Cursor-shaped result of one statement."""

    def __init__(self, rows: list):
        self._rows = [r if isinstance(r, Row) else Row(r) for r in rows]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    @property
    def rowcount(self) -> int:
        # Plain DML comes back from exec_sql() as [{"rowcount": N}].
        if len(self._rows) == 1 and list(self._rows[0].keys()) == ["rowcount"]:
            return self._rows[0]["rowcount"]
        return len(self._rows)


# ── raw-SQL connection facade ────────────────────────────────────
class SqlConn:
    """A sqlite3.Connection-shaped facade. Each ``execute()`` ships one
    statement to the ``exec_sql()`` RPC over HTTPS.

    Use as a context manager — there are no client-side transactions
    (every statement autocommits server-side), so ``commit()`` is a
    no-op kept for call-site compatibility.
    """

    def __init__(self, client: Client):
        self._client = client

    def execute(self, sql: str, params=None) -> _Result:
        q = translate(sql)
        if params:
            q = inline_params(q, params)
        data = self._client.rpc("exec_sql", {"q": q}).execute().data
        return _Result(data or [])

    def cursor(self) -> SqlConn:
        return self

    def commit(self) -> None:
        pass

    def __enter__(self) -> SqlConn:
        return self

    def __exit__(self, *exc) -> bool:
        return False


def connect(url: str, key: str) -> SqlConn:
    """A raw-SQL connection facade backed by Supabase over HTTPS."""
    return SqlConn(get_client(url, key))
