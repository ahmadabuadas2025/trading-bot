"""Database helpers for the Streamlit dashboard.

All reads use a read-only URI connection; all writes use a separate
read-write connection so the WAL file is never corrupted.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

DB_PATH: Path = Path("data/bot.db")


def open_ro() -> sqlite3.Connection | None:
    """Return a read-only SQLite connection, or None if DB is missing."""
    if not DB_PATH.exists():
        return None
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def open_rw() -> sqlite3.Connection:
    """Return a read-write SQLite connection."""
    return sqlite3.connect(str(DB_PATH), timeout=10)


def df(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Execute *sql* and return a DataFrame."""
    with closing(conn.cursor()) as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = (), default=None):
    """Execute *sql* and return the first column of the first row."""
    with closing(conn.cursor()) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else default
