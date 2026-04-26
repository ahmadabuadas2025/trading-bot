"""Hot Trading / Momentum Engine — fast entries on volume spikes."""

from __future__ import annotations

import time

from core.config import HotTradingConfig
from core.logger import LoggerFactory
from core.models import StrategyName, TradeRecord, TradeSide, TradeSignal
from core.portfolio_manager import PortfolioManager
from core.risk_manager import RiskManager
from data.jupiter_client import JupiterClient
from data.liquidity_tracker import LiquidityTracker
from execution.jupiter_executor import JupiterExecutor
from safety.anti_rug import AntiRugEngine
from strategies.base_strategy import BaseStrategy

log = LoggerFactory.get_logger("hot_trading")


class HotTradingEngine(BaseStrategy):
    """Momentum-based trading strategy.

    Detection criteria:
    - Volume spike >= 3x in < 5 minutes.
    - Breakout above recent high.
    - Sudden liquidity inflow.

    TP: 1%–3%, SL: 0.5%–1%, Max hold: 30–120 seconds.
    """

    def __init__(
        self,
        config: HotTradingConfig,
        risk_manager: RiskManager,
        portfolio_manager: PortfolioManager,
        executor: JupiterExecutor,
        anti_rug: AntiRugEngine,
        liquidity_tracker: LiquidityTracker,
        jupiter_client: JupiterClient,
    ) -> None:
        super().__init__(risk_manager, portfolio_manager, executor, anti_rug)
        self._config = config
        self._liquidity_tracker = liquidity_tracker
        self._jupiter = jupiter_client
        self._open_trades: dict[str, dict[str, object]] = {}
        self._volume_history: dict[str, list[tuple[float, float]]] = {}

    @property
    def name(self) -> str:
        return StrategyName.HOT_TRADING.value

    def record_volume(self, token_address: str, volume: float) -> None:
        """Record a volume data point for momentum tracking."""
        now = time.time()
        if token_address not in self._volume_history:
            self._volume_history[token_address] = []
        self._volume_history[token_address].append((now, volume))
        cutoff = now - self._config.volume_spike_window_seconds
        self._volume_history[token_address] = [
            (t, v) for t, v in self._volume_history[token_address] if t >= cutoff
        ]

    def _detect_volume_spike(self, token_address: str) -> bool:
        """Check if volume has spiked above the multiplier threshold."""
        history = self._volume_history.get(token_address, [])
        if len(history) < 2:
            return False
        volumes = [v for _, v in history]
        avg = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
        if avg <= 0:
            return False
        return volumes[-1] >= avg * self._config.volume_spike_multiplier

    async def scan(self) -> list[TradeSignal]:
        """Scan for momentum signals based on volume spikes and liquidity."""
        signals: list[TradeSignal] = []

        if not self._config.enabled:
            return signals

        for token_address in list(self._volume_history.keys()):
            if token_address in self._open_trades:
                continue

            has_spike = self._detect_volume_spike(token_address)
            has_liquidity_spike = self._liquidity_tracker.detect_liquidity_spike(token_address)

            if has_spike or has_liquidity_spike:
                is_safe, _ = await self._anti_rug.validate_token(token_address)
                if not is_safe:
                    continue

                balance = self._portfolio.get_balance()
                amount = balance * 0.03

                signals.append(
                    TradeSignal(
                        strategy=StrategyName.HOT_TRADING,
                        token_address=token_address,
                        side=TradeSide.BUY,
                        amount_usd=amount,
                        confidence=0.6,
                        reason="Volume spike detected" if has_spike else "Liquidity spike",
                    )
                )

        return signals

    async def execute(self, signal: TradeSignal) -> None:
        """Execute a momentum trade with tight TP/SL."""
        allowed = await self._risk.check_trade_allowed(self.name, signal.amount_usd)
        if not allowed:
            return

        if not self._portfolio.allocate(self.name, signal.amount_usd):
            return

        self._risk.record_trade_open(self.name)
        log.info("Hot trade entry: {} ${:.2f}", signal.token_address[:8], signal.amount_usd)

        record = await self._executor.execute_swap(
            input_mint="So11111111111111111111111111111111111111112",
            output_mint=signal.token_address,
            amount_usd=signal.amount_usd,
        )

        if record:
            self._open_trades[signal.token_address] = {
                "record": record,
                "entry_time": time.time(),
                "amount_usd": signal.amount_usd,
            }
            self._portfolio.add_position(self.name, {
                "token_address": signal.token_address,
                "entry_price": record.price,
                "amount_usd": signal.amount_usd,
            })

    async def check_exits(self) -> None:
        """Check TP/SL/timeout conditions for hot trades."""
        now = time.time()
        for token_address, data in list(self._open_trades.items()):
            record: TradeRecord = data["record"]  # type: ignore[assignment]
            entry_time: float = data["entry_time"]  # type: ignore[assignment]
            amount_usd: float = data["amount_usd"]  # type: ignore[assignment]

            if record.price <= 0:
                continue

            elapsed = now - entry_time
            if elapsed >= self._config.max_hold_seconds:
                log.info("Timeout exit for {} after {:.0f}s", token_address[:8], elapsed)
                await self._close_position(token_address, record, amount_usd, 0.0)
                continue

            current_price = await self._jupiter.get_token_price(token_address)
            if current_price <= 0:
                continue

            pnl_pct = (current_price - record.price) / record.price

            if pnl_pct >= self._config.take_profit_pct:
                log.info("TP hit for {}: {:.2%}", token_address[:8], pnl_pct)
                await self._close_position(token_address, record, amount_usd, pnl_pct)
            elif pnl_pct <= self._config.stop_loss_pct:
                log.info("SL hit for {}: {:.2%}", token_address[:8], pnl_pct)
                await self._close_position(token_address, record, amount_usd, pnl_pct)

    async def _close_position(
        self,
        token_address: str,
        record: TradeRecord,
        amount_usd: float,
        pnl_pct: float,
    ) -> None:
        """Close a hot trade position by selling the token."""
        await self._executor.execute_swap(
            input_mint=token_address,
            output_mint="So11111111111111111111111111111111111111112",
            amount_usd=amount_usd,
        )

        actual_pnl = amount_usd * pnl_pct
        self._portfolio.release(self.name, amount_usd, actual_pnl)
        self._portfolio.remove_position(self.name, token_address)
        await self._risk.record_trade_result(self.name, actual_pnl)
        del self._open_trades[token_address]
        log.info("Hot trade closed {}: PnL ${:.2f} ({:.2%})", token_address[:8], actual_pnl, pnl_pct)
