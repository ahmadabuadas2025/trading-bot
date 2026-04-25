"""Database schema initialization for SolanaJupiterBot."""

from __future__ import annotations

from core.database import Database
from core.logger import LoggerFactory

log = LoggerFactory.get_logger("schema")

_TABLES: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS trades (
        id TEXT PRIMARY KEY,
        strategy TEXT NOT NULL,
        token_address TEXT NOT NULL,
        token_symbol TEXT DEFAULT '',
        side TEXT NOT NULL,
        amount_usd REAL NOT NULL,
        amount_token REAL DEFAULT 0,
        price REAL DEFAULT 0,
        pnl REAL DEFAULT 0,
        fees REAL DEFAULT 0,
        slippage_bps INTEGER DEFAULT 0,
        tx_signature TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        execution_time_ms REAL DEFAULT 0,
        timestamp TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        balance_usd REAL NOT NULL,
        equity_usd REAL NOT NULL,
        daily_pnl REAL DEFAULT 0,
        weekly_pnl REAL DEFAULT 0,
        total_pnl REAL DEFAULT 0,
        open_positions INTEGER DEFAULT 0,
        win_count INTEGER DEFAULT 0,
        loss_count INTEGER DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id TEXT PRIMARY KEY,
        strategy TEXT NOT NULL,
        token_address TEXT NOT NULL,
        token_symbol TEXT DEFAULT '',
        side TEXT NOT NULL,
        amount_usd REAL DEFAULT 0,
        confidence REAL DEFAULT 0,
        reason TEXT DEFAULT '',
        timestamp TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS arbitrage_history (
        id TEXT PRIMARY KEY,
        input_mint TEXT NOT NULL,
        output_mint TEXT NOT NULL,
        route_buy TEXT DEFAULT '',
        route_sell TEXT DEFAULT '',
        buy_amount REAL DEFAULT 0,
        expected_profit_pct REAL DEFAULT 0,
        expected_profit_usd REAL DEFAULT 0,
        actual_profit_usd REAL,
        status TEXT DEFAULT 'pending',
        timestamp TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS risk_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        details TEXT DEFAULT '',
        timestamp TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS token_risk_scores (
        token_address TEXT PRIMARY KEY,
        score INTEGER NOT NULL,
        checks_json TEXT DEFAULT '{}',
        is_safe INTEGER DEFAULT 0,
        timestamp TEXT NOT NULL
    )
    """,
]


class SchemaManager:
    """Creates all required database tables on startup."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def initialize(self) -> None:
        """Create all tables if they do not exist."""
        for ddl in _TABLES:
            await self._db.execute(ddl)
        log.info("Database schema initialized ({} tables)", len(_TABLES))
