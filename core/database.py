"""Async SQLite database layer using aiosqlite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from core.logger import LoggerFactory

log = LoggerFactory.get_logger("database")


class Database:
    """Async wrapper around aiosqlite for the bot's persistence layer."""

    def __init__(self, db_path: str = "data/bot.db") -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open a connection to the SQLite database."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        log.info("Database connected at {}", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            log.info("Database connection closed")

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        """Execute a single SQL statement."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor

    async def executemany(self, sql: str, params_list: list[tuple[Any, ...]]) -> None:
        """Execute a SQL statement for each set of parameters."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        await self._conn.executemany(sql, params_list)
        await self._conn.commit()

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Fetch a single row as a dictionary."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Fetch all rows as a list of dictionaries."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    @property
    def connection(self) -> aiosqlite.Connection | None:
        """Return the raw aiosqlite connection."""
        return self._conn
