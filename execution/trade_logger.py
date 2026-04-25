"""Trade logging to SQLite database."""

from __future__ import annotations

from core.database import Database
from core.logger import LoggerFactory
from core.models import TradeRecord

log = LoggerFactory.get_logger("trade_logger")


class TradeLogger:
    """Log every trade to the SQLite database.

    Provides persistence and retrieval for all trade records across strategies.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def log_trade(self, trade: TradeRecord) -> None:
        """Persist a trade record to the database.

        Args:
            trade: The completed trade record to log.
        """
        try:
            await self._db.execute(
                """INSERT INTO trades
                   (id, strategy, token_address, token_symbol, side, amount_usd,
                    amount_token, price, pnl, fees, slippage_bps, tx_signature,
                    status, execution_time_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade.id,
                    trade.strategy.value if hasattr(trade.strategy, "value") else str(trade.strategy),
                    trade.token_address,
                    trade.token_symbol,
                    trade.side.value,
                    trade.amount_usd,
                    trade.amount_token,
                    trade.price,
                    trade.pnl,
                    trade.fees,
                    trade.slippage_bps,
                    trade.tx_signature,
                    trade.status.value,
                    trade.execution_time_ms,
                    trade.timestamp.isoformat(),
                ),
            )
            log.debug("Logged trade {} for {}", trade.id[:8], trade.strategy)
        except Exception:
            log.exception("Failed to log trade {}", trade.id[:8] if trade.id else "unknown")

    async def get_trade_history(
        self, strategy: str | None = None, limit: int = 100
    ) -> list[TradeRecord]:
        """Retrieve trade history from the database.

        Args:
            strategy: Optional strategy filter.
            limit: Maximum number of records to return.

        Returns:
            List of TradeRecord objects.
        """
        if strategy:
            rows = await self._db.fetch_all(
                "SELECT * FROM trades WHERE strategy = ? ORDER BY timestamp DESC LIMIT ?",
                (strategy, limit),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )

        records: list[TradeRecord] = []
        for row in rows:
            try:
                records.append(
                    TradeRecord(
                        id=row["id"],
                        strategy=row["strategy"],
                        token_address=row["token_address"],
                        token_symbol=row.get("token_symbol", ""),
                        side=row["side"],
                        amount_usd=row["amount_usd"],
                        amount_token=row.get("amount_token", 0),
                        price=row.get("price", 0),
                        pnl=row.get("pnl", 0),
                        fees=row.get("fees", 0),
                        slippage_bps=row.get("slippage_bps", 0),
                        tx_signature=row.get("tx_signature", ""),
                        status=row.get("status", "executed"),
                        execution_time_ms=row.get("execution_time_ms", 0),
                    )
                )
            except Exception:
                log.warning("Failed to parse trade row: {}", row.get("id", "unknown"))
        return records

    async def get_trade_count(self, strategy: str | None = None) -> int:
        """Get the total number of trades, optionally filtered by strategy."""
        if strategy:
            row = await self._db.fetch_one(
                "SELECT COUNT(*) as cnt FROM trades WHERE strategy = ?",
                (strategy,),
            )
        else:
            row = await self._db.fetch_one("SELECT COUNT(*) as cnt FROM trades")
        return row["cnt"] if row else 0
