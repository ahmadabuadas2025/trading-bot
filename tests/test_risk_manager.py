"""Tests for risk management rules enforcement."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import RiskConfig
from core.risk_manager import RiskManager


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_risk_per_trade_pct=0.02,
        max_daily_drawdown_pct=0.05,
        max_open_trades_per_strategy=3,
        auto_shutdown_on_breach=True,
    )


@pytest.fixture
def mock_db() -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def risk_manager(risk_config: RiskConfig, mock_db: MagicMock) -> RiskManager:
    return RiskManager(risk_config, mock_db, starting_balance=1000.0)


class TestRiskManager:
    """Test risk rule enforcement."""

    @pytest.mark.asyncio
    async def test_trade_allowed_within_limits(self, risk_manager: RiskManager) -> None:
        allowed = await risk_manager.check_trade_allowed("copy_trading", 15.0)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_trade_rejected_exceeds_max_risk(self, risk_manager: RiskManager) -> None:
        # Max risk = 1000 * 0.02 = 20
        allowed = await risk_manager.check_trade_allowed("copy_trading", 25.0)
        assert allowed is False

    @pytest.mark.asyncio
    async def test_trade_rejected_max_open_trades(self, risk_manager: RiskManager) -> None:
        risk_manager.record_trade_open("hot_trading")
        risk_manager.record_trade_open("hot_trading")
        risk_manager.record_trade_open("hot_trading")

        allowed = await risk_manager.check_trade_allowed("hot_trading", 10.0)
        assert allowed is False

    @pytest.mark.asyncio
    async def test_trade_allowed_different_strategy(self, risk_manager: RiskManager) -> None:
        risk_manager.record_trade_open("hot_trading")
        risk_manager.record_trade_open("hot_trading")
        risk_manager.record_trade_open("hot_trading")

        # Different strategy should still be allowed
        allowed = await risk_manager.check_trade_allowed("copy_trading", 10.0)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_daily_drawdown_triggers_shutdown(self, risk_manager: RiskManager) -> None:
        # 5% drawdown = $50 loss on $1000 balance
        await risk_manager.record_trade_result("copy_trading", -55.0)

        assert risk_manager.is_shutdown() is True

    @pytest.mark.asyncio
    async def test_trade_rejected_after_shutdown(self, risk_manager: RiskManager) -> None:
        await risk_manager.record_trade_result("copy_trading", -55.0)

        allowed = await risk_manager.check_trade_allowed("copy_trading", 5.0)
        assert allowed is False

    def test_daily_drawdown_calculation(self, risk_manager: RiskManager) -> None:
        risk_manager._daily_pnl = -30.0
        drawdown = risk_manager.get_daily_drawdown()
        assert abs(drawdown - 0.03) < 0.001

    def test_reset_daily(self, risk_manager: RiskManager) -> None:
        risk_manager._daily_pnl = -30.0
        risk_manager._shutdown = True

        risk_manager.reset_daily()

        assert risk_manager._daily_pnl == 0.0
        assert risk_manager.is_shutdown() is False

    @pytest.mark.asyncio
    async def test_record_trade_result_decrements_open(self, risk_manager: RiskManager) -> None:
        risk_manager.record_trade_open("gem_detector")
        assert risk_manager._open_trades.get("gem_detector") == 1

        await risk_manager.record_trade_result("gem_detector", 5.0)
        assert risk_manager._open_trades.get("gem_detector") == 0
