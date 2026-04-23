"""Async SQLite wrapper.

A single writer (:class:`Database`) guarded by an ``asyncio.Lock``.
WAL mode is enabled so concurrent readers (such as the Streamlit
dashboard) never block the writer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import aiosqlite


class Database:
    """Thin async wrapper over :mod:`aiosqlite`.

    The class serialises every write through an ``asyncio.Lock`` so the
    rest of the codebase can fire-and-forget writes from any task
    without corrupting SQLite's journal.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Create the wrapper.

        Args:
            db_path: Filesystem path to the SQLite database.
        """
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the underlying connection and set pragmas."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.commit()

    async def close(self) -> None:
        """Close the underlying connection if it is open."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require(self) -> aiosqlite.Connection:
        """Return the open connection or raise.

        Returns:
            The live aiosqlite connection.
        """
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def executescript(self, script: str) -> None:
        """Run a multi-statement script under the writer lock.

        Args:
            script: A semicolon-terminated SQL script.
        """
        async with self._lock:
            conn = self._require()
            await conn.executescript(script)
            await conn.commit()

    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        """Execute a single write and return the last row id.

        Args:
            sql: A single SQL statement.
            params: Optional positional parameter tuple.

        Returns:
            The last row id from the write.
        """
        async with self._lock:
            conn = self._require()
            cur = await conn.execute(sql, params or ())
            await conn.commit()
            return cur.lastrowid or 0

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        """Execute many writes in a single transaction.

        Args:
            sql: The statement to run per row.
            seq: An iterable of parameter tuples.
        """
        async with self._lock:
            conn = self._require()
            await conn.executemany(sql, list(seq))
            await conn.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] | None = None) -> dict | None:
        """Fetch a single row as a dict.

        Args:
            sql: A ``SELECT`` statement.
            params: Optional positional parameters.

        Returns:
            The row as a dict, or ``None`` when no rows match.
        """
        conn = self._require()
        async with conn.execute(sql, params or ()) as cur:
            row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        """Fetch all rows as a list of dicts.

        Args:
            sql: A ``SELECT`` statement.
            params: Optional positional parameters.

        Returns:
            A list of row dicts (possibly empty).
        """
        conn = self._require()
        async with conn.execute(sql, params or ()) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
