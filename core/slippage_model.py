"""Paper-mode slippage and fee model.

Produces realistic executed prices and fees for simulated swaps so
paper P&L approximates what the live bot would see.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of a simulated buy or sell.

    Attributes:
        market_price: The observed mid price.
        executed_price: Price after slippage.
        slippage_pct: Effective slippage fraction applied.
        fee_usd: Total fee charged in USD.
        size_tokens: Token amount received/sold.
        size_usd: Dollar notional of the fill.
    """

    market_price: float
    executed_price: float
    slippage_pct: float
    fee_usd: float
    size_tokens: float
    size_usd: float


class SlippageModel:
    """Compute slippage + fees for paper trades.

    See ``config.yaml → paper_trading`` for tuning knobs.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Create a slippage model.

        Args:
            config: The ``paper_trading`` section from ``config.yaml``.
        """
        self._base = float(config.get("base_slippage_pct", 0.005))
        self._low_liq = float(config.get("low_liq_threshold", 50000))
        self._low_liq_extra = float(config.get("low_liq_extra_slippage", 0.03))
        self._very_low_liq = float(config.get("very_low_liq_threshold", 20000))
        self._very_low_liq_extra = float(config.get("very_low_liq_extra_slippage", 0.05))
        self._price_impact_factor = float(config.get("price_impact_factor", 0.10))
        self._base_fee_sol = float(config.get("solana_base_fee_sol", 0.000005))
        self._priority_fee_sol = float(config.get("solana_priority_fee_sol", 0.0001))
        self._jupiter_fee_pct = float(config.get("jupiter_fee_pct", 0.002))
        self._fallback_sol_usd = float(config.get("fallback_sol_usd", 150.0))

    def compute_slippage(self, liquidity_usd: float, trade_size_usd: float) -> float:
        """Return the effective slippage fraction for a trade.

        Args:
            liquidity_usd: Pool liquidity in USD at the moment of trade.
            trade_size_usd: Dollar notional of the trade.

        Returns:
            Combined slippage (0.005 = 0.5%).
        """
        slip = self._base
        if liquidity_usd < self._very_low_liq:
            slip += self._very_low_liq_extra + self._low_liq_extra
        elif liquidity_usd < self._low_liq:
            slip += self._low_liq_extra
        liquidity_usd = max(liquidity_usd, 1.0)
        slip += (trade_size_usd / liquidity_usd) * self._price_impact_factor
        return float(slip)

    def compute_fee_usd(self, trade_size_usd: float, sol_price_usd: float | None) -> float:
        """Return total fees (Solana fees + Jupiter platform fee).

        Args:
            trade_size_usd: Dollar notional of the trade.
            sol_price_usd: Current SOL price, or ``None`` for fallback.

        Returns:
            Fee in USD.
        """
        sol_usd = sol_price_usd if sol_price_usd is not None else self._fallback_sol_usd
        solana_fee_usd = (self._base_fee_sol + self._priority_fee_sol) * sol_usd
        return solana_fee_usd + (trade_size_usd * self._jupiter_fee_pct)

    def simulate(
        self,
        side: str,
        market_price: float,
        trade_size_usd: float,
        liquidity_usd: float,
        sol_price_usd: float | None = None,
    ) -> ExecutionResult:
        """Simulate a single paper fill.

        Args:
            side: ``buy`` or ``sell``.
            market_price: Observed mid price.
            trade_size_usd: Dollar notional requested.
            liquidity_usd: Current pool liquidity in USD.
            sol_price_usd: Optional live SOL price for fee conversion.

        Returns:
            An :class:`ExecutionResult` describing the simulated fill.
        """
        if side not in {"buy", "sell"}:
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        slippage = self.compute_slippage(liquidity_usd, trade_size_usd)
        direction = 1.0 if side == "buy" else -1.0
        executed_price = market_price * (1.0 + direction * slippage)
        executed_price = max(executed_price, 1e-12)
        fee = self.compute_fee_usd(trade_size_usd, sol_price_usd)
        net_usd = max(trade_size_usd - fee, 0.0) if side == "buy" else trade_size_usd
        size_tokens = net_usd / executed_price if side == "buy" else trade_size_usd / market_price
        return ExecutionResult(
            market_price=market_price,
            executed_price=executed_price,
            slippage_pct=slippage,
            fee_usd=fee,
            size_tokens=size_tokens,
            size_usd=trade_size_usd,
        )
