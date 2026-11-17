"""Where the local web office keeps its conversation and its approvals.

SQLite by default, in ``.somame/office.db`` inside the folder: the standard
library has it, and a laptop or a Raspberry Pi needs nothing else. A home
server or a small office that wants one database for several offices can
name a PostgreSQL database instead (``--database postgresql://...``), with
``pip install "somame-core[postgres]"``. Each office keeps its rows under
its own name, so several can share one database.

The audit log is not here: it stays the hash-chained file the specialist
writes, which ``somame-local --verify`` checks.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS office_messages (
        id {serial} PRIMARY KEY,
        office TEXT NOT NULL,
        at TEXT NOT NULL,
        role TEXT NOT NULL,
        author TEXT NOT NULL,
        text TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS office_approvals (
        id {serial} PRIMARY KEY,
        office TEXT NOT NULL,
        at TEXT NOT NULL,
        tool TEXT NOT NULL,
        target TEXT NOT NULL,
        reason TEXT NOT NULL,
        args TEXT NOT NULL,
        state TEXT NOT NULL,
        answered_by TEXT NOT NULL DEFAULT '',
        answered_at TEXT NOT NULL DEFAULT ''
    )""",
)


class StoreError(RuntimeError):
    """The office's database could not be opened."""


class Store(Protocol):
    def add_message(self, role: str, author: str, text: str) -> int: ...

    def messages(self, after: int = 0, limit: int = 200) -> list[dict[str, Any]]: ...

    def add_approval(self, tool: str, target: str, reason: str, args: dict[str, Any]) -> int: ...

    def answer_approval(self, approval_id: int, state: str, by: str) -> None: ...

    def approvals(self, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _SQL:
    """The queries both databases share; only the placeholder differs."""

    mark = "?"

    def __init__(self, office: str) -> None:
        self.office = office
        self.lock = threading.Lock()

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        raise NotImplementedError

    def _insert(self, sql: str, values: tuple[Any, ...]) -> int:
        raise NotImplementedError

    def _q(self, sql: str) -> str:
        return sql.replace("?", self.mark)

    def add_message(self, role: str, author: str, text: str) -> int:
        return self._insert(
            "INSERT INTO office_messages (office, at, role, author, text) VALUES (?, ?, ?, ?, ?)",
            (self.office, _now(), role, author[:200], text),
        )

    def messages(self, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(
                self._q(
                    "SELECT id, at, role, author, text FROM office_messages "
                    "WHERE office = ? AND id > ? ORDER BY id LIMIT ?"
                ),
                (self.office, after, limit),
            )
            rows = cur.fetchall()
        return [{"id": r[0], "at": r[1], "role": r[2], "author": r[3], "text": r[4]} for r in rows]

    def add_approval(self, tool: str, target: str, reason: str, args: dict[str, Any]) -> int:
        return self._insert(
            "INSERT INTO office_approvals (office, at, tool, target, reason, args, state) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (self.office, _now(), tool, target, reason, json.dumps(args, default=str)),
        )

    def answer_approval(self, approval_id: int, state: str, by: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                self._q(
                    "UPDATE office_approvals SET state = ?, answered_by = ?, answered_at = ? "
                    "WHERE office = ? AND id = ?"
                ),
                (state, by[:200], _now(), self.office, approval_id),
            )

    def approvals(self, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, at, tool, target, reason, args, state, answered_by, answered_at "
            "FROM office_approvals WHERE office = ?"
        )
        values: tuple[Any, ...] = (self.office,)
        if state:
            sql += " AND state = ?"
            values += (state,)
        with self._cursor() as cur:
            cur.execute(self._q(sql + " ORDER BY id DESC LIMIT ?"), (*values, limit))
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "at": r[1],
                "tool": r[2],
                "target": r[3],
                "reason": r[4],
                "args": json.loads(r[5]),
                "state": r[6],
                "answered_by": r[7],
                "answered_at": r[8],
            }
            for r in rows
        ]


class SqliteStore(_SQL):
    def __init__(self, path: Path, office: str) -> None:
        super().__init__(office)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        with self._cursor() as cur:
            for statement in SCHEMA:
                cur.execute(statement.format(serial="INTEGER"))

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        with self.lock:
            cur = self.db.cursor()
            try:
                yield cur
                self.db.commit()
            finally:
                cur.close()

    def _insert(self, sql: str, values: tuple[Any, ...]) -> int:
        with self._cursor() as cur:
            cur.execute(sql, values)
            return int(cur.lastrowid or 0)

    def close(self) -> None:
        self.db.close()


class PostgresStore(_SQL):
    mark = "%s"

    def __init__(self, url: str, office: str) -> None:
        super().__init__(office)
        try:
            import psycopg  # type: ignore[import-not-found,unused-ignore]
        except ImportError as exc:
            raise StoreError(
                'Install the PostgreSQL driver: pip install "somame-core[postgres]".'
            ) from exc
        try:
            self.db = psycopg.connect(url, autocommit=True)
        except Exception as exc:
            raise StoreError(f"Could not open the database: {exc}") from exc
        with self._cursor() as cur:
            for statement in SCHEMA:
                cur.execute(statement.format(serial="BIGSERIAL"))

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        with self.lock, self.db.cursor() as cur:
            yield cur

    def _insert(self, sql: str, values: tuple[Any, ...]) -> int:
        with self._cursor() as cur:
            cur.execute(self._q(sql) + " RETURNING id", values)
            return int(cur.fetchone()[0])

    def close(self) -> None:
        self.db.close()


def open_store(database: str, folder: Path, office: str) -> Store:
    """``database`` empty: SQLite in the folder's ``.somame``. Otherwise a
    ``postgresql://`` address."""
    if not database:
        return SqliteStore(folder / ".somame" / "office.db", office)
    if database.startswith(("postgresql://", "postgres://")):
        return PostgresStore(database, office)
    raise StoreError("--database takes a postgresql:// address; leave it out for SQLite.")
