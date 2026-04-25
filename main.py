"""CLI entry point for SolanaJupiterBot.

Usage:
    python main.py --mode paper --config config.yaml
    python main.py --mode live --config config.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import signal

from arbitrage.arbitrage_engine import ArbitrageEngine
from arbitrage.mev_protection import MEVProtection
from arbitrage.route_scanner import RouteScanner
from core.config import ConfigManager
from core.database import Database
from core.logger import LoggerFactory
from core.portfolio_manager import PortfolioManager
from core.risk_manager import RiskManager
from core.schema import SchemaManager
from data.jupiter_client import JupiterClient
from data.liquidity_tracker import LiquidityTracker
from data.solana_data import SolanaDataFeed
from data.wallet_tracker import WalletTracker
from execution.jupiter_executor import JupiterExecutor
from safety.anti_rug import AntiRugEngine
from safety.honeypot_detector import HoneypotDetector
from safety.token_validator import TokenValidator
from strategies.copy_trading import CopyTradingEngine
from strategies.gem_detector import GemDetectorEngine
from strategies.hot_trading import HotTradingEngine


class BotOrchestrator:
    """Main orchestrator that wires all modules and runs the trading loop."""

    def __init__(self, config_path: str, mode: str | None = None) -> None:
        self._config_manager = ConfigManager(config_path, mode)
        self._shutdown_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Bootstrap all modules and start trading engines."""
        config = self._config_manager.load()
        LoggerFactory.setup(config.app.log_level, config.app.log_path)
        log = LoggerFactory.get_logger("main")

        log.info("Starting SolanaJupiterBot v2.0 in {} mode", config.app.mode)

        # Database
        db = Database(config.app.db_path)
        await db.connect()
        schema = SchemaManager(db)
        await schema.initialize()

        # Core managers
        starting_balance = config.paper_trading.starting_balance_usd
        risk_manager = RiskManager(config.risk, db, starting_balance)
        portfolio_manager = PortfolioManager(db, starting_balance)

        # Data layer
        jupiter_client = JupiterClient(config.jupiter)
        await jupiter_client.start()

        solana_data = SolanaDataFeed()
        await solana_data.start()

        wallet_tracker = WalletTracker(config.copy_trading.tracked_wallets)
        await wallet_tracker.start()

        liquidity_tracker = LiquidityTracker()
        await liquidity_tracker.start()

        # Safety
        token_validator = TokenValidator(config.safety, solana_data)
        honeypot_detector = HoneypotDetector(config.safety, jupiter_client)
        anti_rug = AntiRugEngine(token_validator, honeypot_detector)

        # Execution
        executor = JupiterExecutor(config)

        # Strategies
        copy_engine = CopyTradingEngine(
            config=config.copy_trading,
            risk_manager=risk_manager,
            portfolio_manager=portfolio_manager,
            executor=executor,
            anti_rug=anti_rug,
            wallet_tracker=wallet_tracker,
            liquidity_tracker=liquidity_tracker,
            jupiter_client=jupiter_client,
        )

        hot_engine = HotTradingEngine(
            config=config.hot_trading,
            risk_manager=risk_manager,
            portfolio_manager=portfolio_manager,
            executor=executor,
            anti_rug=anti_rug,
            liquidity_tracker=liquidity_tracker,
            jupiter_client=jupiter_client,
        )

        gem_engine = GemDetectorEngine(
            config=config.gem_detector,
            risk_manager=risk_manager,
            portfolio_manager=portfolio_manager,
            executor=executor,
            anti_rug=anti_rug,
            solana_data=solana_data,
            liquidity_tracker=liquidity_tracker,
            jupiter_client=jupiter_client,
        )

        # Arbitrage
        route_scanner = RouteScanner(jupiter_client)
        mev_protection = MEVProtection(config.arbitrage, jupiter_client, liquidity_tracker)
        arb_engine = ArbitrageEngine(
            config=config.arbitrage,
            db=db,
            risk_manager=risk_manager,
            portfolio_manager=portfolio_manager,
            route_scanner=route_scanner,
            mev_protection=mev_protection,
            executor=executor,
        )

        strategies = [copy_engine, hot_engine, gem_engine]

        log.info("All modules initialized successfully")
        log.info(
            "Strategies: Copy={}, Hot={}, Gem={}, Arb={}",
            config.copy_trading.enabled,
            config.hot_trading.enabled,
            config.gem_detector.enabled,
            config.arbitrage.enabled,
        )

        # Start concurrent loops
        self._tasks = [
            asyncio.create_task(self._strategy_loop(strategies, risk_manager, log)),
            asyncio.create_task(arb_engine.start()),
            asyncio.create_task(self._portfolio_snapshot_loop(portfolio_manager, log)),
        ]

        try:
            await self._shutdown_event.wait()
        finally:
            log.info("Shutting down...")
            arb_engine.stop()
            for task in self._tasks:
                task.cancel()

            await jupiter_client.close()
            await solana_data.close()
            await wallet_tracker.close()
            await liquidity_tracker.close()
            await db.close()
            log.info("Shutdown complete")

    async def _strategy_loop(
        self,
        strategies: list[CopyTradingEngine | HotTradingEngine | GemDetectorEngine],
        risk_manager: RiskManager,
        log: object,
    ) -> None:
        """Run strategy scan/execute/check_exits loops."""
        while not self._shutdown_event.is_set():
            if risk_manager.is_shutdown():
                await asyncio.sleep(5)
                continue

            for strategy in strategies:
                try:
                    signals = await strategy.scan()
                    for sig in signals:
                        await strategy.execute(sig)
                    await strategy.check_exits()
                except Exception:
                    LoggerFactory.get_logger("main").exception(
                        "Error in strategy {}", strategy.name
                    )

            await asyncio.sleep(2)

    async def _portfolio_snapshot_loop(
        self, portfolio: PortfolioManager, log: object
    ) -> None:
        """Periodically save portfolio snapshots."""
        while not self._shutdown_event.is_set():
            try:
                await portfolio.save_snapshot()
            except Exception:
                LoggerFactory.get_logger("main").exception("Portfolio snapshot failed")
            await asyncio.sleep(30)

    def shutdown(self) -> None:
        """Signal the bot to shut down gracefully."""
        self._shutdown_event.set()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="SolanaJupiterBot — Automated Solana trading via Jupiter DEX"
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default=None,
        help="Trading mode (overrides config.yaml)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()
    bot = BotOrchestrator(args.config, args.mode)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, bot.shutdown)

    try:
        loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        bot.shutdown()
        loop.run_until_complete(asyncio.sleep(1))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
