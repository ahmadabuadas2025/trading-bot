"""Global portfolio state management for SolanaJupiterBot."""

from __future__ import annotations

from datetime import UTC, datetime

from core.database import Database
from core.logger import LoggerFactory
from core.models import PortfolioSnapshot

log = LoggerFactory.get_logger("portfolio")


class PortfolioManager:
    """Track total balance, per-strategy allocation, and open positions."""

    def __init__(self, db: Database, starting_balance: float) -> None:
        self._db = db
        self._balance: float = starting_balance
        self._starting_balance: float = starting_balance
        self._allocations: dict[str, float] = {}
        self._open_positions: dict[str, list[dict[str, object]]] = {}
        self._total_pnl: float = 0.0
        self._daily_pnl: float = 0.0
        self._win_count: int = 0
        self._loss_count: int = 0

    def get_balance(self) -> float:
        """Return current available balance in USD."""
        return self._balance

    def allocate(self, strategy: str, amount: float) -> bool:
        """Allocate funds from available balance to a strategy."""
        if amount > self._balance:
            log.warning(
                "Insufficient balance: requested ${:.2f}, available ${:.2f}",
                amount,
                self._balance,
            )
            return False
        self._balance -= amount
        self._allocations[strategy] = self._allocations.get(strategy, 0.0) + amount
        log.info("Allocated ${:.2f} to {} (remaining: ${:.2f})", amount, strategy, self._balance)
        return True

    def release(self, strategy: str, amount: float, pnl: float = 0.0) -> None:
        """Release allocated funds back to available balance with PnL."""
        returned = amount + pnl
        self._balance += returned
        alloc = self._allocations.get(strategy, 0.0)
        self._allocations[strategy] = max(0.0, alloc - amount)
        self._total_pnl += pnl
        self._daily_pnl += pnl
        if pnl >= 0:
            self._win_count += 1
        else:
            self._loss_count += 1
        log.info(
            "Released ${:.2f} from {} (pnl: ${:.2f}, balance: ${:.2f})",
            amount,
            strategy,
            pnl,
            self._balance,
        )

    def get_open_positions(self, strategy: str | None = None) -> list[dict[str, object]]:
        """Get open positions, optionally filtered by strategy."""
        if strategy:
            return list(self._open_positions.get(strategy, []))
        positions: list[dict[str, object]] = []
        for strat_positions in self._open_positions.values():
            positions.extend(strat_positions)
        return positions

    def add_position(self, strategy: str, position: dict[str, object]) -> None:
        """Track a new open position."""
        if strategy not in self._open_positions:
            self._open_positions[strategy] = []
        self._open_positions[strategy].append(position)

    def remove_position(self, strategy: str, token_address: str) -> None:
        """Remove a closed position."""
        if strategy in self._open_positions:
            self._open_positions[strategy] = [
                p for p in self._open_positions[strategy] if p.get("token_address") != token_address
            ]

    def get_snapshot(self) -> PortfolioSnapshot:
        """Return a snapshot of the current portfolio state."""
        total_open = sum(len(pos) for pos in self._open_positions.values())
        equity = self._balance + sum(self._allocations.values())
        return PortfolioSnapshot(
            balance_usd=self._balance,
            equity_usd=equity,
            daily_pnl=self._daily_pnl,
            total_pnl=self._total_pnl,
            open_positions=total_open,
            win_count=self._win_count,
            loss_count=self._loss_count,
            updated_at=datetime.now(UTC),
        )

    async def save_snapshot(self) -> None:
        """Persist the current portfolio snapshot to the database."""
        snap = self.get_snapshot()
        await self._db.execute(
            """INSERT INTO portfolio
               (balance_usd, equity_usd, daily_pnl, weekly_pnl, total_pnl,
                open_positions, win_count, loss_count, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snap.balance_usd,
                snap.equity_usd,
                snap.daily_pnl,
                snap.weekly_pnl,
                snap.total_pnl,
                snap.open_positions,
                snap.win_count,
                snap.loss_count,
                snap.updated_at.isoformat(),
            ),
        )

    def reset_daily(self) -> None:
        """Reset daily PnL tracking."""
        self._daily_pnl = 0.0
