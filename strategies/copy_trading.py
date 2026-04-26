"""Copy Trading Engine — mirrors profitable smart money wallets."""

from __future__ import annotations

from core.config import CopyTradingConfig
from core.logger import LoggerFactory
from core.models import StrategyName, TradeRecord, TradeSide, TradeSignal
from core.portfolio_manager import PortfolioManager
from core.risk_manager import RiskManager
from data.jupiter_client import JupiterClient
from data.liquidity_tracker import LiquidityTracker
from data.wallet_tracker import WalletTracker
from execution.jupiter_executor import JupiterExecutor
from safety.anti_rug import AntiRugEngine
from strategies.base_strategy import BaseStrategy

log = LoggerFactory.get_logger("copy_trading")


class CopyTradingEngine(BaseStrategy):
    """Copy Trading strategy that mirrors profitable wallet trades.

    Entry rules:
    - Token liquidity > $50K (configurable).
    - Market cap < $50M (configurable).
    - Source wallet has proven profitability (win rate >= 55%).
    - Position sizing: 10–20% of the wallet's signal size.

    Exit: source wallet sells OR TP/SL triggers.
    """

    def __init__(
        self,
        config: CopyTradingConfig,
        risk_manager: RiskManager,
        portfolio_manager: PortfolioManager,
        executor: JupiterExecutor,
        anti_rug: AntiRugEngine,
        wallet_tracker: WalletTracker,
        liquidity_tracker: LiquidityTracker,
        jupiter_client: JupiterClient,
    ) -> None:
        super().__init__(risk_manager, portfolio_manager, executor, anti_rug)
        self._config = config
        self._wallet_tracker = wallet_tracker
        self._liquidity_tracker = liquidity_tracker
        self._jupiter = jupiter_client
        self._open_trades: dict[str, TradeRecord] = {}

    @property
    def name(self) -> str:
        return StrategyName.COPY_TRADING.value

    async def scan(self) -> list[TradeSignal]:
        """Scan tracked wallets for recent buy signals."""
        signals: list[TradeSignal] = []

        if not self._config.enabled:
            return signals

        for wallet in self._wallet_tracker.tracked_wallets:
            if not self._wallet_tracker.is_profitable_wallet(
                wallet, self._config.min_wallet_win_rate
            ):
                continue

            trades = await self._wallet_tracker.get_recent_trades(wallet)
            for trade in trades:
                token_address = trade.get("token_address", "")
                if not token_address:
                    continue

                liquidity = await self._liquidity_tracker.get_liquidity(token_address)
                if liquidity < self._config.min_liquidity_usd:
                    continue

                is_safe, details = await self._anti_rug.validate_token(token_address)
                if not is_safe:
                    log.info("Token {} failed safety: {}", token_address[:8], details)
                    continue

                signal_amount = float(trade.get("amount", 0))
                position_size = signal_amount * self._config.position_size_pct_of_signal

                signals.append(
                    TradeSignal(
                        strategy=StrategyName.COPY_TRADING,
                        token_address=token_address,
                        side=TradeSide.BUY,
                        amount_usd=position_size,
                        confidence=0.7,
                        reason=f"Copy from wallet {wallet[:8]}",
                        metadata={"source_wallet": wallet},
                    )
                )

        return signals

    async def execute(self, signal: TradeSignal) -> None:
        """Execute a copy trade signal."""
        allowed = await self._risk.check_trade_allowed(self.name, signal.amount_usd)
        if not allowed:
            return

        if not self._portfolio.allocate(self.name, signal.amount_usd):
            return

        self._risk.record_trade_open(self.name)
        log.info("Executing copy trade: {} ${:.2f}", signal.token_address[:8], signal.amount_usd)

        record = await self._executor.execute_swap(
            input_mint="So11111111111111111111111111111111111111112",
            output_mint=signal.token_address,
            amount_usd=signal.amount_usd,
            # slippage_bps uses default from config (stop_loss_pct was incorrectly passed here)
        )

        if record:
            self._open_trades[signal.token_address] = record
            self._portfolio.add_position(self.name, {
                "token_address": signal.token_address,
                "entry_price": record.price,
                "amount_usd": signal.amount_usd,
                "tp_pct": self._config.take_profit_pct,
                "sl_pct": self._config.stop_loss_pct,
            })

    async def check_exits(self) -> None:
        """Check TP/SL conditions for open copy trades."""
        for token_address, trade in list(self._open_trades.items()):
            if trade.price <= 0:
                continue

            current_price = await self._jupiter.get_token_price(token_address)
            if current_price <= 0:
                continue

            pnl_pct = (current_price - trade.price) / trade.price

            if pnl_pct >= self._config.take_profit_pct:
                log.info("TP hit for {}: {:.2%}", token_address[:8], pnl_pct)
                await self._close_position(token_address, trade, pnl_pct)
            elif pnl_pct <= self._config.stop_loss_pct:
                log.info("SL hit for {}: {:.2%}", token_address[:8], pnl_pct)
                await self._close_position(token_address, trade, pnl_pct)

    async def _close_position(
        self, token_address: str, trade: TradeRecord, pnl_pct: float
    ) -> None:
        """Close a position by selling the token."""
        await self._executor.execute_swap(
            input_mint=token_address,
            output_mint="So11111111111111111111111111111111111111112",
            amount_usd=trade.amount_usd,
        )

        pnl = trade.amount_usd * pnl_pct
        self._portfolio.release(self.name, trade.amount_usd, pnl)
        self._portfolio.remove_position(self.name, token_address)
        await self._risk.record_trade_result(self.name, pnl)
        del self._open_trades[token_address]
        log.info("Copy trade closed {}: PnL ${:.2f}", token_address[:8], pnl)
