"""Hidden Gem Detector — finds undervalued newly listed tokens."""

from __future__ import annotations

from core.config import GemDetectorConfig
from core.logger import LoggerFactory
from core.models import StrategyName, TradeRecord, TradeSide, TradeSignal
from core.portfolio_manager import PortfolioManager
from core.risk_manager import RiskManager
from data.jupiter_client import JupiterClient
from data.liquidity_tracker import LiquidityTracker
from data.solana_data import SolanaDataFeed
from execution.jupiter_executor import JupiterExecutor
from safety.anti_rug import AntiRugEngine
from strategies.base_strategy import BaseStrategy

log = LoggerFactory.get_logger("gem_detector")


class GemDetectorEngine(BaseStrategy):
    """Detects hidden gem tokens meeting quality criteria.

    Filters:
    - Recently listed new token pairs.
    - Liquidity >= $30K.
    - Holders > 100.
    - No obvious scam indicators (passes anti-rug).

    Execution: 1–3% of portfolio. TP: 2x–5x, SL: -30%.
    """

    def __init__(
        self,
        config: GemDetectorConfig,
        risk_manager: RiskManager,
        portfolio_manager: PortfolioManager,
        executor: JupiterExecutor,
        anti_rug: AntiRugEngine,
        solana_data: SolanaDataFeed,
        liquidity_tracker: LiquidityTracker,
        jupiter_client: JupiterClient,
    ) -> None:
        super().__init__(risk_manager, portfolio_manager, executor, anti_rug)
        self._config = config
        self._solana_data = solana_data
        self._liquidity_tracker = liquidity_tracker
        self._jupiter = jupiter_client
        self._open_trades: dict[str, dict[str, object]] = {}
        self._candidate_tokens: list[str] = []

    @property
    def name(self) -> str:
        return StrategyName.GEM_DETECTOR.value

    def add_candidate(self, token_address: str) -> None:
        """Add a token address to the candidate list for scanning."""
        if token_address not in self._candidate_tokens:
            self._candidate_tokens.append(token_address)

    async def scan(self) -> list[TradeSignal]:
        """Scan candidate tokens for hidden gem criteria."""
        signals: list[TradeSignal] = []

        if not self._config.enabled:
            return signals

        for token_address in list(self._candidate_tokens):
            if token_address in self._open_trades:
                continue

            liquidity = await self._liquidity_tracker.get_liquidity(token_address)
            if liquidity < self._config.min_liquidity_usd:
                continue

            holders = await self._solana_data.get_token_holders(token_address)
            if holders < self._config.min_holders:
                continue

            is_safe, details = await self._anti_rug.validate_token(token_address)
            if not is_safe:
                log.info("Gem candidate {} failed safety: {}", token_address[:8], details)
                continue

            balance = self._portfolio.get_balance()
            amount = balance * self._config.allocation_pct

            signals.append(
                TradeSignal(
                    strategy=StrategyName.GEM_DETECTOR,
                    token_address=token_address,
                    side=TradeSide.BUY,
                    amount_usd=amount,
                    confidence=0.5,
                    reason=f"Gem detected: liq=${liquidity:.0f}, holders={holders}",
                    metadata={"liquidity": liquidity, "holders": holders},
                )
            )

        return signals

    async def execute(self, signal: TradeSignal) -> None:
        """Execute a gem trade with small allocation."""
        allowed = await self._risk.check_trade_allowed(self.name, signal.amount_usd)
        if not allowed:
            return

        if not self._portfolio.allocate(self.name, signal.amount_usd):
            return

        self._risk.record_trade_open(self.name)
        log.info("Gem trade entry: {} ${:.2f}", signal.token_address[:8], signal.amount_usd)

        record = await self._executor.execute_swap(
            input_mint="So11111111111111111111111111111111111111112",
            output_mint=signal.token_address,
            amount_usd=signal.amount_usd,
        )

        if record:
            self._open_trades[signal.token_address] = {
                "record": record,
                "amount_usd": signal.amount_usd,
                "entry_price": record.price,
            }
            self._portfolio.add_position(self.name, {
                "token_address": signal.token_address,
                "entry_price": record.price,
                "amount_usd": signal.amount_usd,
            })

    async def check_exits(self) -> None:
        """Check TP/SL conditions for gem trades."""
        for token_address, data in list(self._open_trades.items()):
            record: TradeRecord = data["record"]  # type: ignore[assignment]
            amount_usd: float = data["amount_usd"]  # type: ignore[assignment]
            entry_price: float = data["entry_price"]  # type: ignore[assignment]

            if entry_price <= 0:
                continue

            current_price = await self._jupiter.get_token_price(token_address)
            if current_price <= 0:
                continue

            multiplier = current_price / entry_price

            if multiplier >= self._config.take_profit_multiplier:
                pnl_pct = multiplier - 1.0
                log.info("Gem TP hit for {}: {:.1f}x", token_address[:8], multiplier)
                await self._close_position(token_address, record, amount_usd, pnl_pct)
            elif (current_price - entry_price) / entry_price <= self._config.stop_loss_pct:
                pnl_pct = (current_price - entry_price) / entry_price
                log.info("Gem SL hit for {}: {:.2%}", token_address[:8], pnl_pct)
                await self._close_position(token_address, record, amount_usd, pnl_pct)

    async def _close_position(
        self,
        token_address: str,
        record: TradeRecord,
        amount_usd: float,
        pnl_pct: float,
    ) -> None:
        """Close a gem position."""
        pnl = amount_usd * pnl_pct
        self._portfolio.release(self.name, amount_usd, pnl)
        self._portfolio.remove_position(self.name, token_address)
        await self._risk.record_trade_result(self.name, pnl)
        del self._open_trades[token_address]
        log.info("Gem closed {}: PnL ${:.2f}", token_address[:8], pnl)
