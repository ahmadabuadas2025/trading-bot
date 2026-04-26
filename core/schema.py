"""SQLite schema and initial data for SolanaMemBot.

Exposes :class:`SchemaManager` which creates every table and seeds
the four fund buckets on first run. Re-running is safe because every
statement uses ``IF NOT EXISTS``.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.db import Database

SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS fund_buckets (
    id             INTEGER PRIMARY KEY,
    bucket_name    TEXT NOT NULL UNIQUE,
    allocation_pct REAL NOT NULL,
    balance        REAL NOT NULL,
    enabled        INTEGER DEFAULT 1,
    description    TEXT,
    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_name    TEXT NOT NULL,
    coin_address   TEXT NOT NULL,
    coin_symbol    TEXT,
    entry_price    REAL NOT NULL,
    size_tokens    REAL NOT NULL,
    size_usd       REAL NOT NULL,
    fees_paid      REAL DEFAULT 0,
    stop_loss_pct  REAL,
    take_profit_pct REAL,
    atr            REAL,
    peak_price     REAL,
    trailing_armed INTEGER DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'OPEN',
    opened_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at      DATETIME,
    exit_price     REAL,
    pnl_usd        REAL,
    pnl_pct        REAL,
    close_reason   TEXT,
    extra_json     TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_coin ON positions(coin_address);
CREATE INDEX IF NOT EXISTS idx_positions_bucket ON positions(bucket_name);

CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id    INTEGER,
    bucket_name    TEXT NOT NULL,
    coin_address   TEXT NOT NULL,
    coin_symbol    TEXT,
    side           TEXT NOT NULL,
    mode           TEXT NOT NULL,
    market_price   REAL,
    executed_price REAL,
    size_tokens    REAL,
    size_usd       REAL,
    slippage_pct   REAL,
    fee_usd        REAL,
    tx_sig         TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);

CREATE TABLE IF NOT EXISTS price_ticks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    coin_address   TEXT NOT NULL,
    price_usd      REAL NOT NULL,
    liquidity_usd  REAL,
    volume_1h_usd  REAL,
    ts             DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ticks_coin_ts ON price_ticks(coin_address, ts);

CREATE TABLE IF NOT EXISTS scores (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    coin_address   TEXT NOT NULL,
    coin_symbol    TEXT,
    bucket_name    TEXT,
    profile        TEXT NOT NULL,
    social         REAL,
    wallet         REAL,
    momentum       REAL,
    safety         REAL,
    acceleration   REAL,
    final_score    REAL NOT NULL,
    threshold      REAL,
    passed         INTEGER DEFAULT 0,
    extra_json     TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scores_coin ON scores(coin_address);

CREATE TABLE IF NOT EXISTS llm_scan_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time      DATETIME NOT NULL,
    coin_address   TEXT NOT NULL,
    coin_symbol    TEXT,
    bucket         TEXT NOT NULL,
    llm_score      INTEGER,
    verdict        TEXT,
    confidence     TEXT,
    social_buzz    TEXT,
    kol_mentioned  INTEGER DEFAULT 0,
    red_flags      TEXT,
    reason         TEXT,
    best_entry     TEXT,
    math_score     REAL,
    market_regime  TEXT,
    approved       INTEGER DEFAULT 0,
    expires_at     DATETIME,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_llm_coin ON llm_scan_results(coin_address);
CREATE INDEX IF NOT EXISTS idx_llm_expires ON llm_scan_results(expires_at);

CREATE TABLE IF NOT EXISTS social_data_cache (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    coin_address       TEXT NOT NULL,
    coin_symbol        TEXT,
    reddit_posts       INTEGER DEFAULT 0,
    reddit_upvotes     REAL DEFAULT 0,
    reddit_top_comment TEXT,
    has_twitter        INTEGER DEFAULT 0,
    twitter_followers  INTEGER DEFAULT 0,
    has_telegram       INTEGER DEFAULT 0,
    telegram_members   INTEGER DEFAULT 0,
    has_website        INTEGER DEFAULT 0,
    social_links       INTEGER DEFAULT 0,
    coingecko_score    REAL,
    lunar_volume       REAL,
    data_quality       TEXT DEFAULT 'none',
    collected_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at         DATETIME
);
CREATE INDEX IF NOT EXISTS idx_social_coin ON social_data_cache(coin_address);

CREATE TABLE IF NOT EXISTS blacklist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin_address    TEXT NOT NULL,
    coin_symbol     TEXT,
    reason          TEXT NOT NULL,
    source          TEXT NOT NULL,
    blacklisted_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at      DATETIME,
    permanent       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bl_coin ON blacklist(coin_address);

CREATE TABLE IF NOT EXISTS safety_state (
    id              INTEGER PRIMARY KEY,
    emergency_stop  INTEGER DEFAULT 0,
    daily_loss_pct  REAL DEFAULT 0,
    stop_reason     TEXT,
    triggered_at    DATETIME,
    reset_at        DATETIME
);

CREATE TABLE IF NOT EXISTS bucket_cooldowns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_name     TEXT NOT NULL,
    reason          TEXT,
    cooldown_until  DATETIME,
    triggered_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cool_bucket ON bucket_cooldowns(bucket_name);

CREATE TABLE IF NOT EXISTS regime_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    regime          TEXT NOT NULL,
    btc_change_24h  REAL,
    sol_change_24h  REAL,
    fear_greed      INTEGER,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wallets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    address         TEXT NOT NULL UNIQUE,
    win_rate        REAL,
    trades_7d       INTEGER,
    avg_hold_minutes INTEGER,
    rug_count_30d   INTEGER DEFAULT 0,
    enabled         INTEGER DEFAULT 1,
    refreshed_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    component       TEXT,
    level           TEXT,
    message         TEXT,
    payload_json    TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass(frozen=True)
class SeedBucket:
    """Initial row for the ``fund_buckets`` table.

    Attributes:
        name: Bucket key, matches ``buckets`` in config.yaml.
        allocation_pct: Fraction of starting capital.
        description: Short human-friendly description.
    """

    name: str
    allocation_pct: float
    description: str


DEFAULT_BUCKETS: tuple[SeedBucket, ...] = (
    SeedBucket("HOT_TRADER", 0.10, "Fast scalping on trending coins."),
    SeedBucket("COPY_TRADER", 0.30, "Mirror top active wallets."),
    SeedBucket("GEM_HUNTER", 0.40, "Hidden low-cap gems, LLM validated."),
    SeedBucket("NEW_LISTING", 0.20, "Brand new listings, LLM validated."),
)


class SchemaManager:
    """Create schema and seed initial rows."""

    def __init__(self, db: Database) -> None:
        """Create a schema manager.

        Args:
            db: An already-connected :class:`Database`.
        """
        self._db = db

    async def initialize(self, starting_balance_usd: float) -> None:
        """Create tables and seed fund buckets on first run.

        Args:
            starting_balance_usd: Paper-mode starting balance.
        """
        await self._db.executescript(SCHEMA_SQL)
        await self._seed_buckets(starting_balance_usd)
        await self._db.execute(
            "INSERT OR IGNORE INTO safety_state (id, emergency_stop) VALUES (1, 0)"
        )

    async def _seed_buckets(self, starting_balance_usd: float) -> None:
        """Insert default buckets if they do not already exist.

        Args:
            starting_balance_usd: Total paper balance to split.
        """
        existing = await self._db.fetchall("SELECT bucket_name FROM fund_buckets")
        if existing:
            return
        rows = [
            (
                b.name,
                b.allocation_pct,
                round(starting_balance_usd * b.allocation_pct, 6),
                1,
                b.description,
            )
            for b in DEFAULT_BUCKETS
        ]
        await self._db.executemany(
            "INSERT INTO fund_buckets "
            "(bucket_name, allocation_pct, balance, enabled, description) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
