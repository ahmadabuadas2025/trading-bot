"""Risk management engine for SolanaJupiterBot."""

from __future__ import annotations

from datetime import UTC, datetime

from core.config import RiskConfig
from core.database import Database
from core.logger import LoggerFactory

log = LoggerFactory.get_logger("risk_manager")


class RiskManager:
    """Enforces risk limits across all strategies.

    Rules:
    - Max 2% risk per trade (configurable).
    - Max 5% daily drawdown — auto-shutdown on breach.
    - Max 3 open trades per strategy.
    """

    def __init__(self, config: RiskConfig, db: Database, starting_balance: float) -> None:
        self._config = config
        self._db = db
        self._starting_balance = starting_balance
        self._daily_pnl: float = 0.0
        self._open_trades: dict[str, int] = {}
        self._shutdown: bool = False

    async def check_trade_allowed(self, strategy: str, amount_usd: float) -> bool:
        """Check whether a new trade is permitted under risk rules."""
        if self._shutdown:
            log.warning("Trading is shut down — trade rejected for {}", strategy)
            return False

        max_risk = self._starting_balance * self._config.max_risk_per_trade_pct
        if amount_usd > max_risk:
            log.warning(
                "Trade amount ${:.2f} exceeds max risk ${:.2f} for {}",
                amount_usd,
                max_risk,
                strategy,
            )
            return False

        open_count = self._open_trades.get(strategy, 0)
        if open_count >= self._config.max_open_trades_per_strategy:
            log.warning(
                "Strategy {} has {} open trades (max {})",
                strategy,
                open_count,
                self._config.max_open_trades_per_strategy,
            )
            return False

        return True

    async def record_trade_result(self, strategy: str, pnl: float) -> None:
        """Record a completed trade result and update daily PnL."""
        self._daily_pnl += pnl

        if self._open_trades.get(strategy, 0) > 0:
            self._open_trades[strategy] -= 1

        drawdown = self.get_daily_drawdown()
        if drawdown >= self._config.max_daily_drawdown_pct:
            log.error(
                "Daily drawdown {:.2%} exceeds limit {:.2%} — shutting down!",
                drawdown,
                self._config.max_daily_drawdown_pct,
            )
            await self._trigger_shutdown()

    def record_trade_open(self, strategy: str) -> None:
        """Increment the open trade count for a strategy."""
        self._open_trades[strategy] = self._open_trades.get(strategy, 0) + 1

    def get_daily_drawdown(self) -> float:
        """Calculate the current daily drawdown as a fraction of starting balance."""
        if self._starting_balance <= 0:
            return 0.0
        return max(0.0, -self._daily_pnl / self._starting_balance)

    def is_shutdown(self) -> bool:
        """Return whether the risk manager has triggered a shutdown."""
        return self._shutdown

    def force_shutdown(self) -> None:
        """Force shutdown triggered externally (e.g., dashboard emergency stop)."""
        self._shutdown = True
        log.warning("Trading forcefully shut down via external trigger")

    async def _trigger_shutdown(self) -> None:
        """Shut down all trading and log a risk event."""
        self._shutdown = True
        if self._config.auto_shutdown_on_breach:
            now = datetime.now(UTC).isoformat()
            await self._db.execute(
                "INSERT INTO risk_events (type, details, timestamp) VALUES (?, ?, ?)",
                ("daily_drawdown_breach", f"Drawdown: {self.get_daily_drawdown():.4f}", now),
            )
            log.error("All trading disabled due to risk breach")

    def reset_daily(self) -> None:
        """Reset daily PnL tracking (call at start of new trading day)."""
        self._daily_pnl = 0.0
        self._shutdown = False
        log.info("Daily risk counters reset")
