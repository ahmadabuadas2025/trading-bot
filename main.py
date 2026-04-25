"""CLI entry point for SolanaJupiterBot.

Usage:
    python main.py --mode paper --config config.yaml
    python main.py --mode live --config config.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from arbitrage.arbitrage_engine import ArbitrageEngine
from arbitrage.mev_protection import MEVProtection
from arbitrage.route_scanner import RouteScanner
from core.config import BotConfig, ConfigManager
from core.database import Database
from core.logger import LoggerFactory
from core.portfolio_manager import PortfolioManager
from core.risk_manager import RiskManager
from core.schema import SchemaManager
from data.jupiter_client import JupiterClient
from data.liquidity_tracker import LiquidityTracker
from data.solana_data import SolanaDataFeed
from data.token_discovery import TokenDiscoveryWorker
from data.volume_feed import VolumeFeedWorker
from data.wallet_tracker import WalletTracker
from execution.jupiter_executor import JupiterExecutor
from safety.anti_rug import AntiRugEngine
from safety.honeypot_detector import HoneypotDetector
from safety.token_validator import TokenValidator
from strategies.copy_trading import CopyTradingEngine
from strategies.gem_detector import GemDetectorEngine
from strategies.hot_trading import HotTradingEngine


async def _fetch_wallet_balance(config: BotConfig) -> float:
    """Fetch the real SOL balance of the configured wallet and convert to USD."""
    wallet_key = os.getenv("WALLET_PRIVATE_KEY", "")
    if not wallet_key:
        return 0.0

    try:
        from solana.rpc.async_api import AsyncClient
        from solders.keypair import Keypair

        keypair = Keypair.from_base58_string(wallet_key)
        public_key = keypair.pubkey()

        rpc_url = (
            getattr(getattr(config, "solana", None), "rpc_url", None)
            or "https://api.mainnet-beta.solana.com"
        )
        rpc = AsyncClient(rpc_url)

        try:
            response = await rpc.get_balance(public_key)
            lamports = response.value
            sol_balance = lamports / 1e9  # Convert lamports to SOL

            # Get SOL price in USD from Jupiter Price API
            import aiohttp

            price_url = config.jupiter.price_api_url or "https://price.jup.ag/v6"
            sol_mint = "So11111111111111111111111111111111111111112"
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{price_url}/price?ids={sol_mint}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        sol_price = (
                            data.get("data", {})
                            .get(sol_mint, {})
                            .get("price", config.paper_trading.fallback_sol_usd)
                        )
                    else:
                        sol_price = config.paper_trading.fallback_sol_usd

            usd_balance = sol_balance * float(sol_price)
            return usd_balance
        finally:
            await rpc.close()

    except ImportError:
        LoggerFactory.get_logger("main").error(
            "solders/solana-py not installed — cannot fetch live balance"
        )
        return 0.0
    except Exception as e:
        LoggerFactory.get_logger("main").exception(
            "Failed to fetch wallet balance: {}", str(e)
        )
        return 0.0


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

        # Data layer (initialized early so live balance fetch can use the RPC)
        jupiter_client = JupiterClient(config.jupiter)
        await jupiter_client.start()

        # Core managers — determine starting balance based on mode
        if config.app.mode == "live":
            starting_balance = await _fetch_wallet_balance(config)
            log.info("Live mode: wallet balance = ${:.2f}", starting_balance)
            if starting_balance <= 0:
                log.warning(
                    "Wallet balance is $0 — live trades will fail. "
                    "Fund your wallet first."
                )
        else:
            starting_balance = config.paper_trading.starting_balance_usd
            log.info("Paper mode: starting balance = ${:.2f}", starting_balance)

        risk_manager = RiskManager(config.risk, db, starting_balance)
        portfolio_manager = PortfolioManager(db, starting_balance)

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

        # Data ingestion workers
        volume_feed = VolumeFeedWorker(
            hot_engine=hot_engine,
            jupiter_client=jupiter_client,
            birdeye_api_key=os.getenv("BIRDEYE_API_KEY", ""),
        )

        token_discovery = TokenDiscoveryWorker(
            gem_engine=gem_engine,
            hot_engine=hot_engine,
            jupiter_client=jupiter_client,
            liquidity_tracker=liquidity_tracker,
            solana_data=solana_data,
            volume_feed=volume_feed,
            birdeye_api_key=os.getenv("BIRDEYE_API_KEY", ""),
        )

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
            asyncio.create_task(token_discovery.run()),
            asyncio.create_task(volume_feed.run()),
        ]

        try:
            await self._shutdown_event.wait()
        finally:
            log.info("Shutting down...")
            arb_engine.stop()
            token_discovery.stop()
            volume_feed.stop()
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
        cycle_count = 0
        while not self._shutdown_event.is_set():
            if risk_manager.is_shutdown():
                await asyncio.sleep(5)
                continue

            cycle_count += 1
            total_signals = 0

            for strategy in strategies:
                try:
                    signals = await strategy.scan()
                    total_signals += len(signals)
                    for sig in signals:
                        await strategy.execute(sig)
                    await strategy.check_exits()
                except Exception:
                    LoggerFactory.get_logger("main").exception(
                        "Error in strategy {}", strategy.name
                    )

            # Log status every 30 cycles (~60 seconds)
            if cycle_count % 30 == 0:
                LoggerFactory.get_logger("main").info(
                    "Scan cycle #{}: {} signals found across {} strategies",
                    cycle_count,
                    total_signals,
                    len(strategies),
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

    if sys.platform != "win32":
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
