"""Tests for arbitrage route comparison and opportunity detection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from arbitrage.mev_protection import MEVProtection
from arbitrage.route_scanner import RouteScanner
from core.config import ArbitrageConfig
from core.models import ArbitrageOpportunity
from data.jupiter_client import SOL_MINT, USDC_MINT


@pytest.fixture
def mock_jupiter() -> MagicMock:
    client = MagicMock()
    client.get_quote = AsyncMock()
    client.get_routes = AsyncMock(return_value=[])
    return client


@pytest.fixture
def route_scanner(mock_jupiter: MagicMock) -> RouteScanner:
    return RouteScanner(mock_jupiter)


@pytest.fixture
def arb_config() -> ArbitrageConfig:
    return ArbitrageConfig(
        min_profit_threshold_pct=0.003,
        max_capital_pct=0.07,
        max_concurrent_trades=2,
        max_consecutive_failures=5,
        scan_interval_seconds=2,
    )


@pytest.fixture
def mock_liquidity() -> MagicMock:
    tracker = MagicMock()
    tracker.get_liquidity = AsyncMock(return_value=100_000.0)
    return tracker


@pytest.fixture
def mev_protection(
    arb_config: ArbitrageConfig, mock_jupiter: MagicMock, mock_liquidity: MagicMock
) -> MEVProtection:
    return MEVProtection(arb_config, mock_jupiter, mock_liquidity)


class TestRouteScanner:
    """Test route scanning and arbitrage opportunity detection."""

    @pytest.mark.asyncio
    async def test_find_arbitrage_opportunity_profitable(
        self, route_scanner: RouteScanner, mock_jupiter: MagicMock
    ) -> None:
        # Buy: 1000 lamports -> 1050 output tokens
        # Sell: 1050 tokens -> 1010 lamports (1% profit)
        mock_jupiter.get_quote = AsyncMock(
            side_effect=[
                {"outAmount": "1050", "routePlan": [{"label": "Raydium"}]},
                {"outAmount": "1010", "routePlan": [{"label": "Orca"}]},
            ]
        )

        result = await route_scanner.find_arbitrage_opportunity(
            SOL_MINT, USDC_MINT, 1000, min_profit_pct=0.005
        )

        assert result is not None
        assert result.expected_profit_pct == pytest.approx(0.01, abs=0.001)

    @pytest.mark.asyncio
    async def test_find_arbitrage_no_opportunity(
        self, route_scanner: RouteScanner, mock_jupiter: MagicMock
    ) -> None:
        # Buy: 1000 -> 950 output (loss)
        # Sell: 950 -> 900 (still loss)
        mock_jupiter.get_quote = AsyncMock(
            side_effect=[
                {"outAmount": "950", "routePlan": []},
                {"outAmount": "900", "routePlan": []},
            ]
        )

        result = await route_scanner.find_arbitrage_opportunity(
            SOL_MINT, USDC_MINT, 1000, min_profit_pct=0.003
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_find_arbitrage_zero_output(
        self, route_scanner: RouteScanner, mock_jupiter: MagicMock
    ) -> None:
        mock_jupiter.get_quote = AsyncMock(return_value={"outAmount": "0"})

        result = await route_scanner.find_arbitrage_opportunity(
            SOL_MINT, USDC_MINT, 1000
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_scan_routes_returns_list(
        self, route_scanner: RouteScanner, mock_jupiter: MagicMock
    ) -> None:
        mock_jupiter.get_routes = AsyncMock(
            return_value=[{"label": "Raydium"}, {"label": "Orca"}]
        )

        routes = await route_scanner.scan_routes(SOL_MINT, USDC_MINT, 1000)
        assert len(routes) == 2


class TestMEVProtection:
    """Test MEV protection pre-execution validation."""

    @pytest.mark.asyncio
    async def test_valid_opportunity_passes(
        self, mev_protection: MEVProtection, mock_jupiter: MagicMock
    ) -> None:
        mock_jupiter.get_quote = AsyncMock(
            return_value={"outAmount": "1050"}
        )

        opportunity = ArbitrageOpportunity(
            input_mint=SOL_MINT,
            output_mint=USDC_MINT,
            buy_amount=1000,
            expected_output_buy=1050,
        )

        result = await mev_protection.validate_pre_execution(opportunity)
        assert result is True

    @pytest.mark.asyncio
    async def test_price_shift_cancels(
        self, mev_protection: MEVProtection, mock_jupiter: MagicMock
    ) -> None:
        # Price shifted significantly
        mock_jupiter.get_quote = AsyncMock(
            return_value={"outAmount": "900"}  # Was 1050
        )

        opportunity = ArbitrageOpportunity(
            input_mint=SOL_MINT,
            output_mint=USDC_MINT,
            buy_amount=1000,
            expected_output_buy=1050,
        )

        result = await mev_protection.validate_pre_execution(opportunity)
        assert result is False

    @pytest.mark.asyncio
    async def test_low_liquidity_unsafe(
        self, mev_protection: MEVProtection, mock_liquidity: MagicMock
    ) -> None:
        mock_liquidity.get_liquidity = AsyncMock(return_value=5000.0)

        opportunity = ArbitrageOpportunity(
            input_mint=SOL_MINT,
            output_mint=USDC_MINT,
        )

        result = await mev_protection.is_safe_route(opportunity)
        assert result is False
