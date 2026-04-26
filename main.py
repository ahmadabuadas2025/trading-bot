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
from core.dashboard_bridge import DashboardBridge
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
    log = LoggerFactory.get_logger("main")

    wallet_key = os.getenv("WALLET_PRIVATE_KEY", "")
    if not wallet_key:
        log.error("WALLET_PRIVATE_KEY is not set in .env — returning $0")
        return 0.0

    try:
        from solana.rpc.async_api import AsyncClient
        from solders.keypair import Keypair  # noqa: WPS433

        log.info("solders/solana packages loaded OK")
    except ImportError:
        log.error("solders or solana-py not installed. Run: pip install solders solana")
        return 0.0

    try:
        keypair = Keypair.from_base58_string(wallet_key)
        public_key = keypair.pubkey()
        log.info("Wallet public key: {}", str(public_key))

        rpc_url = (
            getattr(getattr(config, "solana", None), "rpc_url", None)
            or "https://api.mainnet-beta.solana.com"
        )
        log.info("Using Solana RPC: {}", rpc_url)
        rpc = AsyncClient(rpc_url)

        try:
            response = await rpc.get_balance(public_key)
            lamports = response.value
            sol_balance = lamports / 1e9
            log.info("SOL balance: {} SOL ({} lamports)", sol_balance, lamports)

            if sol_balance <= 0:
                log.warning("Wallet SOL balance is 0 — check if the correct wallet key is configured")
                return 0.0

            import aiohttp  # noqa: WPS433

            price_url = config.jupiter.price_api_url or "https://price.jup.ag/v6"
            sol_mint = "So11111111111111111111111111111111111111112"

            sol_price = config.paper_trading.fallback_sol_usd  # default fallback
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{price_url}/price?ids={sol_mint}",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        log.info("Jupiter Price API status: {}", resp.status)
                        if resp.status == 200:
                            data = await resp.json()
                            fetched_price = (
                                data.get("data", {})
                                .get(sol_mint, {})
                                .get("price")
                            )
                            if fetched_price:
                                sol_price = float(fetched_price)
                                log.info("SOL price from Jupiter: ${}", sol_price)
                            else:
                                log.warning(
                                    "Jupiter returned no price data, using fallback ${}",
                                    sol_price,
                                )
                        else:
                            body = await resp.text()
                            log.warning(
                                "Jupiter Price API error {}: {} — using fallback ${}",
                                resp.status,
                                body[:200],
                                sol_price,
                            )
            except Exception as price_err:
                log.warning(
                    "Failed to fetch SOL price: {} — using fallback ${}",
                    str(price_err),
                    sol_price,
                )

            usd_balance = sol_balance * sol_price
            log.info(
                "Wallet USD balance: ${:.2f} ({} SOL × ${:.2f})",
                usd_balance,
                sol_balance,
                sol_price,
            )
            return usd_balance
        finally:
            await rpc.close()

    except Exception as e:
        log.exception("Failed to fetch wallet balance: {}", str(e))
        return 0.0


async def _fetch_spl_token_balances(
    rpc: object,
    public_key: object,
    sol_price: float,
    config: BotConfig,
    log: object,
) -> float:
    """Fetch SPL token account balances and return their total USD value."""
    import aiohttp  # noqa: WPS433

    spl_usd_total = 0.0
    try:
        from solana.rpc.types import TokenAccountOpts  # noqa: WPS433
        from solders.pubkey import Pubkey  # noqa: WPS433

        token_program = Pubkey.from_string(
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
        )
        token_resp = await rpc.get_token_accounts_by_owner_json_parsed(
            public_key,
            opts=TokenAccountOpts(program_id=token_program),
        )

        accounts = token_resp.value or []
        if not accounts:
            log.info("No SPL token accounts found")
            return 0.0

        log.info("Found {} SPL token account(s)", len(accounts))

        # Collect mints and balances
        mint_balances: dict[str, float] = {}
        for acct in accounts:
            try:
                parsed = acct.account.data.parsed
                info = parsed["info"]
                mint = info["mint"]
                amount = float(info["tokenAmount"]["uiAmount"] or 0)
                if amount > 0:
                    mint_balances[mint] = mint_balances.get(mint, 0.0) + amount
            except (KeyError, TypeError, ValueError):
                continue

        if not mint_balances:
            return 0.0

        # Fetch prices for all mints from Jupiter
        price_url = config.jupiter.price_api_url or "https://price.jup.ag/v6"
        mint_ids = ",".join(mint_balances.keys())
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{price_url}/price?ids={mint_ids}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                price_data: dict[str, dict[str, object]] = {}
                if resp.status == 200:
                    body = await resp.json()
                    price_data = body.get("data", {})

        for mint, amount in mint_balances.items():
            price = float(price_data.get(mint, {}).get("price", 0) or 0)
            usd_value = amount * price
            if usd_value > 0.01:
                log.info(
                    "SPL token {}: {} (${:.2f})",
                    mint[:8] + "...",
                    amount,
                    usd_value,
                )
                spl_usd_total += usd_value

        log.info("Total SPL token value: ${:.2f}", spl_usd_total)
    except Exception:
        log.warning("Could not fetch SPL token balances")

    return spl_usd_total


class BotOrchestrator:
    """Main orchestrator that wires all modules and runs the trading loop."""

    def __init__(self, config_path: str, mode: str | None = None) -> None:
        self._config_manager = ConfigManager(config_path, mode)
        self._shutdown_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._config: BotConfig | None = None
        self._executor: JupiterExecutor | None = None

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
        dashboard_bridge = DashboardBridge()

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
        self._config = config
        self._executor = executor

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
            asyncio.create_task(self._strategy_loop(strategies, risk_manager, log, dashboard_bridge)),
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
        dashboard_bridge: DashboardBridge,
    ) -> None:
        """Run strategy scan/execute/check_exits loops."""
        cycle_count = 0
        while not self._shutdown_event.is_set():
            # Check dashboard emergency stop
            if dashboard_bridge.is_emergency_stop():
                log.warning("Emergency stop triggered from dashboard!")
                dashboard_bridge.clear_emergency_stop()
                risk_manager.force_shutdown()
                self._shutdown_event.set()
                return

            if risk_manager.is_shutdown():
                await asyncio.sleep(5)
                continue

            cycle_count += 1
            total_signals = 0

            for strategy in strategies:
                strategy_key = strategy.name
                strategy_disabled = not dashboard_bridge.is_strategy_enabled(strategy_key)

                try:
                    # Always run check_exits so open positions are monitored
                    await strategy.check_exits()

                    # Only scan and execute if the strategy is enabled
                    if strategy_disabled:
                        continue

                    signals = await strategy.scan()
                    total_signals += len(signals)
                    for sig in signals:
                        await strategy.execute(sig)
                except Exception:
                    LoggerFactory.get_logger("main").exception(
                        "Error in strategy {}", strategy.name
                    )

            # Log status every 30 cycles (~60 seconds)
            if cycle_count % 30 == 0:
                log.info(
                    "Scan cycle #{}: {} signals across {} strategies",
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
