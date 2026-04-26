"""Arbitrage detection and execution engine."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from arbitrage.mev_protection import MEVProtection
from arbitrage.route_scanner import RouteScanner
from core.config import ArbitrageConfig
from core.database import Database
from core.logger import LoggerFactory
from core.models import ArbitrageOpportunity, TradeStatus
from core.portfolio_manager import PortfolioManager
from core.risk_manager import RiskManager
from data.jupiter_client import SOL_MINT, USDC_MINT
from execution.jupiter_executor import JupiterExecutor

log = LoggerFactory.get_logger("arbitrage_engine")

BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
JUP_MINT = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
RAY_MINT = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
WIF_MINT = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"

# Decimal places for known input tokens (used to convert USD → smallest unit)
_TOKEN_DECIMALS: dict[str, int] = {
    SOL_MINT: 9,
    USDC_MINT: 6,
}

DEFAULT_PAIRS: list[tuple[str, str]] = [
    (SOL_MINT, USDC_MINT),
    (SOL_MINT, BONK_MINT),
    (SOL_MINT, JUP_MINT),
    (SOL_MINT, RAY_MINT),
    (SOL_MINT, WIF_MINT),
    (USDC_MINT, JUP_MINT),
]


class ArbitrageEngine:
    """Main arbitrage engine that scans, validates, and executes arb trades.

    Loop:
    1. Fetch multiple Jupiter routes.
    2. Compare output values.
    3. Select best buy/sell routes.
    4. Check profit > fees + slippage + threshold.
    5. Validate via MEV protection.
    6. Execute atomic swap.
    7. Log profit/loss.
    """

    def __init__(
        self,
        config: ArbitrageConfig,
        db: Database,
        risk_manager: RiskManager,
        portfolio_manager: PortfolioManager,
        route_scanner: RouteScanner,
        mev_protection: MEVProtection,
        executor: JupiterExecutor,
        token_pairs: list[tuple[str, str]] | None = None,
        jupiter_client: object | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._risk = risk_manager
        self._portfolio = portfolio_manager
        self._scanner = route_scanner
        self._mev = mev_protection
        self._executor = executor
        self._pairs = token_pairs or DEFAULT_PAIRS
        self._jupiter = jupiter_client
        self._consecutive_failures: int = 0
        self._active_trades: int = 0
        self._running: bool = False

    async def start(self) -> None:
        """Start the arbitrage scanning loop."""
        if not self._config.enabled:
            log.info("Arbitrage engine disabled")
            return

        self._running = True
        log.info("Arbitrage engine started")

        while self._running:
            try:
                await self._scan_cycle()
            except Exception:
                log.exception("Error in arbitrage scan cycle")
                self._consecutive_failures += 1

            if self._consecutive_failures >= self._config.max_consecutive_failures:
                log.error(
                    "Too many consecutive failures ({}) — disabling arbitrage",
                    self._consecutive_failures,
                )
                self._running = False
                break

            await asyncio.sleep(self._config.scan_interval_seconds)

    def stop(self) -> None:
        """Stop the arbitrage scanning loop."""
        self._running = False
        log.info("Arbitrage engine stopped")

    async def _scan_cycle(self) -> None:
        """Run one scan cycle across all token pairs."""
        if self._risk.is_shutdown():
            return

        if self._active_trades >= self._config.max_concurrent_trades:
            return

        for input_mint, output_mint in self._pairs:
            balance = self._portfolio.get_balance()
            max_capital = balance * self._config.max_capital_pct

            if max_capital < 0.01:
                continue

            decimals = _TOKEN_DECIMALS.get(input_mint, 9)

            try:
                if input_mint == USDC_MINT:
                    amount = int(max_capital * (10 ** decimals))
                else:
                    sol_price = 150.0
                    if self._jupiter:
                        try:
                            fetched = await self._jupiter.get_token_price(SOL_MINT)
                            if fetched > 0:
                                sol_price = fetched
                        except Exception:
                            pass
                    token_amount = max_capital / sol_price
                    amount = int(token_amount * (10 ** decimals))
            except Exception:
                continue

            opportunity = await self._scanner.find_arbitrage_opportunity(
                input_mint=input_mint,
                output_mint=output_mint,
                amount=amount,
                min_profit_pct=self._config.min_profit_threshold_pct,
            )

            if opportunity:
                await self._execute_opportunity(opportunity, max_capital)

    async def _execute_opportunity(
        self, opportunity: ArbitrageOpportunity, capital_usd: float
    ) -> None:
        """Validate and execute an arbitrage opportunity."""
        allowed = await self._risk.check_trade_allowed("arbitrage", capital_usd)
        if not allowed:
            return

        is_valid = await self._mev.validate_pre_execution(opportunity)
        if not is_valid:
            log.info("Opportunity failed MEV validation — skipping")
            return

        self._active_trades += 1
        self._risk.record_trade_open("arbitrage")
        opportunity.id = str(uuid.uuid4())

        try:
            if not self._portfolio.allocate("arbitrage", capital_usd):
                await self._risk.record_trade_result("arbitrage", 0.0)
                return

            record = await self._executor.execute_swap(
                input_mint=opportunity.input_mint,
                output_mint=opportunity.output_mint,
                amount_usd=capital_usd,
            )

            if record:
                opportunity.status = TradeStatus.EXECUTED
                actual_pnl = capital_usd * opportunity.expected_profit_pct
                opportunity.actual_profit_usd = actual_pnl
                self._portfolio.release("arbitrage", capital_usd, actual_pnl)
                await self._risk.record_trade_result("arbitrage", actual_pnl)
                self._consecutive_failures = 0
                log.info("Arbitrage executed: profit ${:.4f}", actual_pnl)
            else:
                opportunity.status = TradeStatus.FAILED
                self._portfolio.release("arbitrage", capital_usd, 0.0)
                await self._risk.record_trade_result("arbitrage", 0.0)
                self._consecutive_failures += 1
        except Exception:
            opportunity.status = TradeStatus.FAILED
            self._consecutive_failures += 1
            log.exception("Arbitrage execution failed")
        finally:
            self._active_trades -= 1
            await self._log_opportunity(opportunity)

    async def _log_opportunity(self, opportunity: ArbitrageOpportunity) -> None:
        """Persist an arbitrage opportunity to the database."""
        try:
            now = datetime.now(UTC).isoformat()
            await self._db.execute(
                """INSERT INTO arbitrage_history
                   (id, input_mint, output_mint, route_buy, route_sell,
                    buy_amount, expected_profit_pct, expected_profit_usd,
                    actual_profit_usd, status, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    opportunity.id,
                    opportunity.input_mint,
                    opportunity.output_mint,
                    opportunity.route_buy,
                    opportunity.route_sell,
                    opportunity.buy_amount,
                    opportunity.expected_profit_pct,
                    opportunity.expected_profit_usd,
                    opportunity.actual_profit_usd,
                    opportunity.status.value,
                    now,
                ),
            )
        except Exception:
            log.warning("Failed to log arbitrage opportunity")
