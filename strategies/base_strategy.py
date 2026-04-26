"""Abstract base strategy class for all trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import TradeSignal
from core.portfolio_manager import PortfolioManager
from core.risk_manager import RiskManager
from execution.jupiter_executor import JupiterExecutor
from safety.anti_rug import AntiRugEngine


class BaseStrategy(ABC):
    """Abstract base class for trading strategy engines.

    All strategies must implement scan(), execute(), and check_exits().
    Common dependencies (risk, portfolio, execution, safety) are injected.
    """

    def __init__(
        self,
        risk_manager: RiskManager,
        portfolio_manager: PortfolioManager,
        executor: JupiterExecutor,
        anti_rug: AntiRugEngine,
    ) -> None:
        self._risk = risk_manager
        self._portfolio = portfolio_manager
        self._executor = executor
        self._anti_rug = anti_rug

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the strategy name."""

    @abstractmethod
    async def scan(self) -> list[TradeSignal]:
        """Scan the market for trade signals.

        Returns:
            List of trade signals detected by this strategy.
        """

    @abstractmethod
    async def execute(self, signal: TradeSignal) -> None:
        """Execute a trade based on a signal.

        Args:
            signal: The trade signal to act on.
        """

    @abstractmethod
    async def check_exits(self) -> None:
        """Check open positions for exit conditions (TP/SL/timeout)."""
