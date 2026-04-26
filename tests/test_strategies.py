"""Tests for strategy signal generation logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import CopyTradingConfig, GemDetectorConfig, HotTradingConfig
from core.models import StrategyName, TradeRecord, TradeSide, TradeStatus
from strategies.copy_trading import CopyTradingEngine
from strategies.gem_detector import GemDetectorEngine
from strategies.hot_trading import HotTradingEngine


@pytest.fixture
def mock_risk_manager() -> MagicMock:
    rm = MagicMock()
    rm.check_trade_allowed = AsyncMock(return_value=True)
    rm.record_trade_open = MagicMock()
    rm.record_trade_result = AsyncMock()
    rm.is_shutdown = MagicMock(return_value=False)
    return rm


@pytest.fixture
def mock_portfolio() -> MagicMock:
    pm = MagicMock()
    pm.get_balance = MagicMock(return_value=1000.0)
    pm.allocate = MagicMock(return_value=True)
    pm.release = MagicMock()
    pm.add_position = MagicMock()
    pm.remove_position = MagicMock()
    return pm


@pytest.fixture
def mock_executor() -> MagicMock:
    ex = MagicMock()
    ex.execute_swap = AsyncMock(
        return_value=TradeRecord(
            id="test-trade-1",
            strategy=StrategyName.COPY_TRADING,
            token_address="test_token",
            side=TradeSide.BUY,
            amount_usd=100.0,
            price=150.0,
            status=TradeStatus.EXECUTED,
        )
    )
    return ex


@pytest.fixture
def mock_anti_rug() -> MagicMock:
    ar = MagicMock()
    ar.validate_token = AsyncMock(return_value=(True, {"overall_safe": True}))
    return ar


@pytest.fixture
def mock_wallet_tracker() -> MagicMock:
    wt = MagicMock()
    wt.tracked_wallets = ["wallet1", "wallet2"]
    wt.is_profitable_wallet = MagicMock(return_value=True)
    wt.get_recent_trades = AsyncMock(
        return_value=[
            {
                "token_address": "token_abc",
                "amount": 500,
                "type": "SWAP",
            }
        ]
    )
    return wt


@pytest.fixture
def mock_liquidity_tracker() -> MagicMock:
    lt = MagicMock()
    lt.get_liquidity = AsyncMock(return_value=100_000.0)
    lt.detect_liquidity_spike = MagicMock(return_value=False)
    return lt


@pytest.fixture
def mock_jupiter_client() -> MagicMock:
    jc = MagicMock()
    jc.get_token_price = AsyncMock(return_value=155.0)
    return jc


@pytest.fixture
def mock_solana_data() -> MagicMock:
    sd = MagicMock()
    sd.get_token_holders = AsyncMock(return_value=500)
    return sd


class TestCopyTradingEngine:
    """Test copy trading signal generation."""

    @pytest.fixture
    def copy_engine(
        self,
        mock_risk_manager: MagicMock,
        mock_portfolio: MagicMock,
        mock_executor: MagicMock,
        mock_anti_rug: MagicMock,
        mock_wallet_tracker: MagicMock,
        mock_liquidity_tracker: MagicMock,
        mock_jupiter_client: MagicMock,
    ) -> CopyTradingEngine:
        config = CopyTradingConfig(
            enabled=True,
            min_liquidity_usd=50000,
            min_wallet_win_rate=0.55,
            position_size_pct_of_signal=0.15,
        )
        return CopyTradingEngine(
            config=config,
            risk_manager=mock_risk_manager,
            portfolio_manager=mock_portfolio,
            executor=mock_executor,
            anti_rug=mock_anti_rug,
            wallet_tracker=mock_wallet_tracker,
            liquidity_tracker=mock_liquidity_tracker,
            jupiter_client=mock_jupiter_client,
        )

    @pytest.mark.asyncio
    async def test_scan_generates_signals(self, copy_engine: CopyTradingEngine) -> None:
        signals = await copy_engine.scan()
        assert len(signals) > 0
        assert signals[0].strategy == StrategyName.COPY_TRADING
        assert signals[0].token_address == "token_abc"

    @pytest.mark.asyncio
    async def test_scan_disabled_returns_empty(self, copy_engine: CopyTradingEngine) -> None:
        copy_engine._config.enabled = False
        signals = await copy_engine.scan()
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_scan_rejects_low_liquidity(
        self, copy_engine: CopyTradingEngine, mock_liquidity_tracker: MagicMock
    ) -> None:
        mock_liquidity_tracker.get_liquidity = AsyncMock(return_value=10_000.0)
        signals = await copy_engine.scan()
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_scan_rejects_unsafe_token(
        self, copy_engine: CopyTradingEngine, mock_anti_rug: MagicMock
    ) -> None:
        mock_anti_rug.validate_token = AsyncMock(return_value=(False, {"honeypot": True}))
        signals = await copy_engine.scan()
        assert len(signals) == 0


class TestHotTradingEngine:
    """Test hot trading volume spike detection."""

    @pytest.fixture
    def hot_engine(
        self,
        mock_risk_manager: MagicMock,
        mock_portfolio: MagicMock,
        mock_executor: MagicMock,
        mock_anti_rug: MagicMock,
        mock_liquidity_tracker: MagicMock,
        mock_jupiter_client: MagicMock,
    ) -> HotTradingEngine:
        config = HotTradingConfig(
            enabled=True,
            volume_spike_multiplier=3.0,
            volume_spike_window_seconds=300,
        )
        return HotTradingEngine(
            config=config,
            risk_manager=mock_risk_manager,
            portfolio_manager=mock_portfolio,
            executor=mock_executor,
            anti_rug=mock_anti_rug,
            liquidity_tracker=mock_liquidity_tracker,
            jupiter_client=mock_jupiter_client,
        )

    @pytest.mark.asyncio
    async def test_volume_spike_generates_signal(self, hot_engine: HotTradingEngine) -> None:
        hot_engine.record_volume("token_x", 100)
        hot_engine.record_volume("token_x", 100)
        hot_engine.record_volume("token_x", 500)  # 5x spike

        signals = await hot_engine.scan()
        assert len(signals) > 0
        assert signals[0].strategy == StrategyName.HOT_TRADING

    @pytest.mark.asyncio
    async def test_no_spike_no_signal(self, hot_engine: HotTradingEngine) -> None:
        hot_engine.record_volume("token_y", 100)
        hot_engine.record_volume("token_y", 105)  # Not a spike

        signals = await hot_engine.scan()
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self, hot_engine: HotTradingEngine) -> None:
        hot_engine._config.enabled = False
        signals = await hot_engine.scan()
        assert len(signals) == 0


class TestGemDetectorEngine:
    """Test gem detector signal generation."""

    @pytest.fixture
    def gem_engine(
        self,
        mock_risk_manager: MagicMock,
        mock_portfolio: MagicMock,
        mock_executor: MagicMock,
        mock_anti_rug: MagicMock,
        mock_solana_data: MagicMock,
        mock_liquidity_tracker: MagicMock,
        mock_jupiter_client: MagicMock,
    ) -> GemDetectorEngine:
        config = GemDetectorConfig(
            enabled=True,
            min_liquidity_usd=30000,
            min_holders=100,
            allocation_pct=0.02,
        )
        return GemDetectorEngine(
            config=config,
            risk_manager=mock_risk_manager,
            portfolio_manager=mock_portfolio,
            executor=mock_executor,
            anti_rug=mock_anti_rug,
            solana_data=mock_solana_data,
            liquidity_tracker=mock_liquidity_tracker,
            jupiter_client=mock_jupiter_client,
        )

    @pytest.mark.asyncio
    async def test_gem_detected(self, gem_engine: GemDetectorEngine) -> None:
        gem_engine.add_candidate("gem_token")
        signals = await gem_engine.scan()
        assert len(signals) > 0
        assert signals[0].strategy == StrategyName.GEM_DETECTOR

    @pytest.mark.asyncio
    async def test_low_liquidity_rejected(
        self, gem_engine: GemDetectorEngine, mock_liquidity_tracker: MagicMock
    ) -> None:
        mock_liquidity_tracker.get_liquidity = AsyncMock(return_value=10_000.0)
        gem_engine.add_candidate("low_liq_token")
        signals = await gem_engine.scan()
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_low_holders_rejected(
        self, gem_engine: GemDetectorEngine, mock_solana_data: MagicMock
    ) -> None:
        mock_solana_data.get_token_holders = AsyncMock(return_value=10)
        gem_engine.add_candidate("few_holders_token")
        signals = await gem_engine.scan()
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self, gem_engine: GemDetectorEngine) -> None:
        gem_engine._config.enabled = False
        gem_engine.add_candidate("any_token")
        signals = await gem_engine.scan()
        assert len(signals) == 0
