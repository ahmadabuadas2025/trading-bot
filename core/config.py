"""Configuration manager for SolanaJupiterBot.

Loads settings from config.yaml and .env, with CLI override support.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """Top-level application settings."""

    name: str = "SolanaJupiterBot"
    timezone: str = "UTC"
    log_level: str = "INFO"
    db_path: str = "data/bot.db"
    log_path: str = "logs/bot.log"
    mode: str = "paper"


class RiskConfig(BaseModel):
    """Risk management parameters."""

    max_risk_per_trade_pct: float = 0.02
    max_daily_drawdown_pct: float = 0.05
    max_open_trades_per_strategy: int = 3
    auto_shutdown_on_breach: bool = True


class PaperTradingConfig(BaseModel):
    """Paper trading parameters."""

    starting_balance_usd: float = 1000.0
    fallback_sol_usd: float = 150.0


class JupiterConfig(BaseModel):
    """Jupiter DEX API configuration."""

    base_url: str = "https://quote-api.jup.ag/v6"
    price_api_url: str = "https://price.jup.ag/v6"
    default_slippage_bps: int = 100
    max_slippage_bps: int = 150
    request_timeout_seconds: int = 15
    max_retries: int = 3


class CopyTradingConfig(BaseModel):
    """Copy trading strategy configuration."""

    enabled: bool = True
    min_liquidity_usd: float = 50000
    max_market_cap_usd: float = 50000000
    min_wallet_win_rate: float = 0.55
    position_size_pct_of_signal: float = 0.15
    stop_loss_pct: float = -0.12
    take_profit_pct: float = 0.35
    tracked_wallets: list[str] = Field(default_factory=list)


class HotTradingConfig(BaseModel):
    """Hot trading / momentum strategy configuration."""

    enabled: bool = True
    volume_spike_multiplier: float = 3.0
    volume_spike_window_seconds: int = 300
    take_profit_pct: float = 0.02
    stop_loss_pct: float = -0.0075
    max_hold_seconds: int = 120


class GemDetectorConfig(BaseModel):
    """Hidden gem detector strategy configuration."""

    enabled: bool = True
    min_liquidity_usd: float = 30000
    min_holders: int = 100
    allocation_pct: float = 0.02
    take_profit_multiplier: float = 3.0
    stop_loss_pct: float = -0.30


class ArbitrageConfig(BaseModel):
    """Arbitrage engine configuration."""

    enabled: bool = True
    min_profit_threshold_pct: float = 0.003
    max_capital_pct: float = 0.07
    max_concurrent_trades: int = 2
    max_consecutive_failures: int = 5
    scan_interval_seconds: int = 2


class SafetyConfig(BaseModel):
    """Safety and anti-rug configuration."""

    max_risk_score: int = 50
    reject_mint_authority: bool = True
    reject_freeze_authority: bool = True
    max_top_holder_pct: float = 0.25
    min_token_age_seconds: int = 30
    restricted_age_seconds: int = 300
    honeypot_check_enabled: bool = True


class DashboardConfig(BaseModel):
    """Dashboard configuration."""

    refresh_interval_seconds: int = 5


class HttpConfig(BaseModel):
    """HTTP client configuration."""

    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    default_timeout_seconds: float = 20


class BotConfig(BaseModel):
    """Root configuration model aggregating all sections."""

    app: AppConfig = Field(default_factory=AppConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    paper_trading: PaperTradingConfig = Field(default_factory=PaperTradingConfig)
    jupiter: JupiterConfig = Field(default_factory=JupiterConfig)
    copy_trading: CopyTradingConfig = Field(default_factory=CopyTradingConfig)
    hot_trading: HotTradingConfig = Field(default_factory=HotTradingConfig)
    gem_detector: GemDetectorConfig = Field(default_factory=GemDetectorConfig)
    arbitrage: ArbitrageConfig = Field(default_factory=ArbitrageConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)


class ConfigManager:
    """Loads and manages bot configuration from YAML and environment."""

    def __init__(self, config_path: str = "config.yaml", mode: str | None = None) -> None:
        self._config_path = Path(config_path)
        self._mode_override = mode
        self._raw: dict[str, Any] = {}
        self.config = BotConfig()

    def load(self) -> BotConfig:
        """Load configuration from YAML file and .env, then validate."""
        load_dotenv()

        if self._config_path.exists():
            with open(self._config_path) as f:
                self._raw = yaml.safe_load(f) or {}

        self.config = BotConfig(**self._raw)

        if self._mode_override:
            self.config.app.mode = self._mode_override

        return self.config

    def get_secret(self, key: str, default: str = "") -> str:
        """Retrieve a secret from environment variables."""
        return os.getenv(key, default)

    @property
    def is_paper_mode(self) -> bool:
        """Check if running in paper trading mode."""
        return self.config.app.mode == "paper"

    @property
    def is_live_mode(self) -> bool:
        """Check if running in live trading mode."""
        return self.config.app.mode == "live"
