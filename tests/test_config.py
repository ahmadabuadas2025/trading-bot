"""Tests for config loading and validation."""

from __future__ import annotations

from pathlib import Path

import yaml

from core.config import BotConfig, ConfigManager


class TestBotConfig:
    """Test Pydantic config model defaults and validation."""

    def test_default_config(self) -> None:
        config = BotConfig()
        assert config.app.name == "SolanaJupiterBot"
        assert config.app.mode == "paper"
        assert config.risk.max_risk_per_trade_pct == 0.02
        assert config.risk.max_daily_drawdown_pct == 0.05
        assert config.risk.max_open_trades_per_strategy == 3

    def test_paper_trading_defaults(self) -> None:
        config = BotConfig()
        assert config.paper_trading.starting_balance_usd == 1000.0
        assert config.paper_trading.fallback_sol_usd == 150.0

    def test_jupiter_defaults(self) -> None:
        config = BotConfig()
        assert config.jupiter.base_url == "https://quote-api.jup.ag/v6"
        assert config.jupiter.default_slippage_bps == 100
        assert config.jupiter.max_retries == 3

    def test_safety_defaults(self) -> None:
        config = BotConfig()
        assert config.safety.max_risk_score == 50
        assert config.safety.reject_mint_authority is True
        assert config.safety.honeypot_check_enabled is True

    def test_arbitrage_defaults(self) -> None:
        config = BotConfig()
        assert config.arbitrage.min_profit_threshold_pct == 0.003
        assert config.arbitrage.max_concurrent_trades == 2


class TestConfigManager:
    """Test ConfigManager YAML loading and mode overrides."""

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        config_data = {
            "app": {"name": "TestBot", "mode": "live", "log_level": "DEBUG"},
            "risk": {"max_risk_per_trade_pct": 0.03},
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        manager = ConfigManager(str(config_file))
        config = manager.load()

        assert config.app.name == "TestBot"
        assert config.app.mode == "live"
        assert config.app.log_level == "DEBUG"
        assert config.risk.max_risk_per_trade_pct == 0.03

    def test_mode_override(self, tmp_path: Path) -> None:
        config_data = {"app": {"mode": "live"}}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        manager = ConfigManager(str(config_file), mode="paper")
        config = manager.load()

        assert config.app.mode == "paper"

    def test_missing_config_file_uses_defaults(self) -> None:
        manager = ConfigManager("/nonexistent/config.yaml")
        config = manager.load()

        assert config.app.name == "SolanaJupiterBot"
        assert config.app.mode == "paper"

    def test_is_paper_mode(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"app": {"mode": "paper"}}))

        manager = ConfigManager(str(config_file))
        manager.load()

        assert manager.is_paper_mode is True
        assert manager.is_live_mode is False

    def test_is_live_mode(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"app": {"mode": "live"}}))

        manager = ConfigManager(str(config_file))
        manager.load()

        assert manager.is_paper_mode is False
        assert manager.is_live_mode is True
