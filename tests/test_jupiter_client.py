"""Tests for Jupiter API client with mocked responses."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import JupiterConfig
from data.jupiter_client import SOL_MINT, USDC_MINT, JupiterClient


@pytest.fixture
def jupiter_config() -> JupiterConfig:
    return JupiterConfig(
        base_url="https://quote-api.jup.ag/v6",
        price_api_url="https://price.jup.ag/v6",
        default_slippage_bps=100,
        request_timeout_seconds=15,
        max_retries=3,
    )


@pytest.fixture
def jupiter_client(jupiter_config: JupiterConfig) -> JupiterClient:
    client = JupiterClient(jupiter_config)
    client._session = MagicMock()
    return client


class TestJupiterClient:
    """Test Jupiter API client methods with mocked HTTP responses."""

    @pytest.mark.asyncio
    async def test_get_quote_success(self, jupiter_client: JupiterClient) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(
            return_value={
                "inputMint": SOL_MINT,
                "outputMint": USDC_MINT,
                "inAmount": "1000000000",
                "outAmount": "15000000",
                "routePlan": [{"label": "Raydium"}],
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        jupiter_client._session.get = MagicMock(return_value=mock_response)

        result = await jupiter_client.get_quote(SOL_MINT, USDC_MINT, 1_000_000_000)

        assert result["outAmount"] == "15000000"
        assert result["inputMint"] == SOL_MINT

    @pytest.mark.asyncio
    async def test_get_token_price(self, jupiter_client: JupiterClient) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(
            return_value={
                "data": {
                    SOL_MINT: {"price": 150.25},
                }
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        jupiter_client._session.get = MagicMock(return_value=mock_response)

        price = await jupiter_client.get_token_price(SOL_MINT)

        assert price == 150.25

    @pytest.mark.asyncio
    async def test_get_token_price_not_found(self, jupiter_client: JupiterClient) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={"data": {}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        jupiter_client._session.get = MagicMock(return_value=mock_response)

        price = await jupiter_client.get_token_price("unknown_token")

        assert price == 0.0

    @pytest.mark.asyncio
    async def test_get_swap_transaction(self, jupiter_client: JupiterClient) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(
            return_value={
                "swapTransaction": "base64_encoded_tx_data",
                "lastValidBlockHeight": 123456789,
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        jupiter_client._session.post = MagicMock(return_value=mock_response)

        quote = {"inputMint": SOL_MINT, "outAmount": "15000000"}
        result = await jupiter_client.get_swap_transaction(quote, "wallet_pubkey_123")

        assert result["swapTransaction"] == "base64_encoded_tx_data"

    @pytest.mark.asyncio
    async def test_get_routes(self, jupiter_client: JupiterClient) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(
            return_value={
                "routePlan": [
                    {"label": "Raydium", "outAmount": "1050"},
                    {"label": "Orca", "outAmount": "1040"},
                ],
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        jupiter_client._session.get = MagicMock(return_value=mock_response)

        routes = await jupiter_client.get_routes(SOL_MINT, USDC_MINT, 1000)

        assert len(routes) == 2
        assert routes[0]["label"] == "Raydium"
