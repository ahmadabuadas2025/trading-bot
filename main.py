"""SolanaTradingBot CLI entry point.

Usage:
    python main.py --mode paper     # default
    python main.py --mode live
    python main.py --llm-dry-run    # print the LLM prompt and exit
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Any

from clients.birdeye import BirdeyeClient
from clients.coingecko import CoinGeckoClient
from clients.dexscreener import DexScreenerClient
from clients.helius import HeliusClient
from clients.jupiter import JupiterClient
from core.atr_calculator import ATRCalculator
from core.blacklist_manager import BlacklistManager
from core.config import AppConfig, ConfigLoader
from core.db import Database
from core.dedup_manager import DedupManager
from core.executor import Executor, LiveExecutor, PaperExecutor
from core.http import HttpClient
from core.llm_client import LLMClient
from core.llm_scanner import LLMScanner
from core.logger import LoggerFactory
from core.orchestrator import BucketRunner, Orchestrator
from core.regime_client import RegimeClient
from core.safety_monitor import SafetyMonitor
from core.schema import SchemaManager
from core.scoring_engine import ScoringEngine
from core.slippage_model import SlippageModel
from core.social_collector import SocialCollector
from core.time_utils import TimeProvider
from services.arbitrage import ArbitrageService
from services.base_bucket import BucketDeps
from services.copy_trading import CopyTradingService, MockWalletProvider
from services.gem_detector import GemDetectorService
from services.hot_trader import HotTraderService
from utils.honeypot import HoneypotChecker


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        The parsed argparse namespace.
    """
    parser = argparse.ArgumentParser(description="SolanaTradingBot")
    parser.add_argument("--mode", choices=["paper", "live"], default=None)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--llm-dry-run", action="store_true")
    return parser.parse_args()


def _banner(cfg: AppConfig, log) -> None:
    """Print a visible banner that clarifies the mode.

    Args:
        cfg: The resolved :class:`AppConfig`.
        log: A bound loguru logger.
    """
    if cfg.mode == "live":
        log.warning("=" * 58)
        log.warning("SolanaTradingBot starting in LIVE MODE — real funds at risk")
        log.warning("=" * 58)
    else:
        log.info("SolanaTradingBot starting in PAPER mode (no real trades)")


def _build_executor(
    cfg: AppConfig, db: Database, slippage: SlippageModel, jupiter: JupiterClient
) -> Executor:
    """Pick the concrete executor for the active mode.

    Args:
        cfg: Application config.
        db: Connected database.
        slippage: Paper-mode slippage model.
        jupiter: Jupiter client for live mode.

    Returns:
        A concrete :class:`Executor`.
    """
    if cfg.mode == "live":
        return LiveExecutor(db, jupiter, cfg.secrets.wallet_private_key)
    return PaperExecutor(db, slippage)


async def _bootstrap(args: argparse.Namespace) -> tuple[AppConfig, dict[str, Any]]:
    """Load configuration, connect DB, and materialise every dependency.

    Args:
        args: Parsed CLI namespace.

    Returns:
        A tuple of (config, context) where ``context`` is a dict of
        live services.
    """
    cfg = ConfigLoader(args.config).load(mode_override=args.mode)
    log_factory = LoggerFactory(cfg.raw["app"]["log_path"], cfg.raw["app"]["log_level"])
    log = log_factory.get("main")
    _banner(cfg, log)

    db = Database(cfg.raw["app"]["db_path"])
    await db.connect()
    starting_balance = float(cfg.raw["paper_trading"]["starting_balance_usd"])
    await SchemaManager(db).initialize(starting_balance)

    http = HttpClient(**cfg.section("http"))
    await http.start()

    dex = DexScreenerClient(http)
    birdeye = BirdeyeClient(http, cfg.secrets.birdeye_api_key)
    helius = HeliusClient(http, cfg.secrets.helius_api_key)
    coingecko = CoinGeckoClient(http)
    jupiter = JupiterClient(http)
    honeypot = HoneypotChecker(http)

    slippage = SlippageModel(cfg.section("paper_trading"))
    scoring = ScoringEngine(cfg.section("scoring"))
    regime = RegimeClient(http, db, cfg.section("regime"))
    time_provider = TimeProvider()
    safety = SafetyMonitor(db, cfg.section("safety"), time_provider)
    dedup = DedupManager(db)
    blacklist = BlacklistManager(db, time_provider)
    atr = ATRCalculator(http, db, cfg.section("atr"), cfg.secrets.birdeye_api_key)

    llm_cfg = cfg.section("llm")
    llm = LLMClient(
        http,
        cfg.secrets.openrouter_api_key,
        llm_cfg.get("base_url", "https://openrouter.ai/api/v1"),
        llm_cfg.get("model", "qwen/qwen3-coder-480b-a35b-instruct:free"),
        llm_cfg.get("fallback_model", "nvidia/nemotron-3-super-120b-a12b:free"),
        int(llm_cfg.get("request_timeout_seconds", 60)),
    )
    social = SocialCollector(
        http, db, dex, coingecko, cfg.section("social_collector"),
        cfg.secrets.lunarcrush_api_key, time_provider,
    )
    scanner = LLMScanner(db, llm, social, regime, llm_cfg, time_provider)

    if args.llm_dry_run:
        prompt = scanner.dry_run_prompt([])
        log.info("LLM dry-run prompt (no API call) length={}", len(prompt))
        print(prompt)  # noqa: T201
        await http.close()
        await db.close()
        sys.exit(0)

    executor = _build_executor(cfg, db, slippage, jupiter)
    if isinstance(executor, PaperExecutor):
        sol_usd = await coingecko.sol_price_usd()
        executor.set_sol_price(sol_usd)

    deps = BucketDeps(
        db=db, executor=executor, dedup=dedup, blacklist=blacklist,
        safety=safety, regime=regime, logger=log_factory, time=time_provider,
    )

    hot = HotTraderService(deps, cfg.bucket("HOT_TRADER"), dex)
    copy = CopyTradingService(
        deps, cfg.bucket("COPY_TRADER"), helius, birdeye, dex, MockWalletProvider()
    )
    gem = GemDetectorService(
        deps, cfg.bucket("GEM_HUNTER"), dex, scoring, scanner, atr, honeypot
    )
    arb = ArbitrageService(deps, cfg.bucket("ARBITRAGE"), dex, jupiter, scoring)
    runners: list[BucketRunner] = [
        BucketRunner(
            hot,
            float(cfg.bucket("HOT_TRADER")["scan_interval_seconds"]),
            float(cfg.bucket("HOT_TRADER")["price_check_interval_seconds"]),
        ),
        BucketRunner(
            copy,
            float(cfg.bucket("COPY_TRADER")["scan_interval_seconds"]),
            float(cfg.bucket("COPY_TRADER")["scan_interval_seconds"]),
        ),
        BucketRunner(
            gem,
            float(cfg.bucket("GEM_HUNTER")["scan_interval_seconds"]),
            60.0,
        ),
        BucketRunner(
            arb,
            float(cfg.bucket("ARBITRAGE")["scan_interval_seconds"]),
            float(cfg.bucket("ARBITRAGE").get("price_check_interval_seconds", 10)),
        ),
    ]
    orch = Orchestrator(runners, regime, safety, scanner, log_factory, cfg.raw, db)
    return cfg, {"db": db, "http": http, "orchestrator": orch, "logger": log_factory}


async def _run_async(args: argparse.Namespace) -> None:
    """Start the orchestrator and wire signal handlers.

    Args:
        args: Parsed CLI namespace.
    """
    _cfg, ctx = await _bootstrap(args)
    orch: Orchestrator = ctx["orchestrator"]
    db: Database = ctx["db"]
    http: HttpClient = ctx["http"]

    loop = asyncio.get_running_loop()

    def _stop(*_: Any) -> None:
        """Signal handler."""
        orch.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:  # Windows
            signal.signal(sig, _stop)
    try:
        await orch.run()
    finally:
        await http.close()
        await db.close()


def main() -> None:
    """Entry point used by ``python main.py``."""
    args = _parse_args()
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    asyncio.run(_run_async(args))


if __name__ == "__main__":
    main()
