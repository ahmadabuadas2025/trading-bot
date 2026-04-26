"""Jupiter DEX API client for quotes, swaps, and price data."""

from __future__ import annotations

from typing import Any

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import JupiterConfig
from core.logger import LoggerFactory

log = LoggerFactory.get_logger("jupiter_client")

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class JupiterClient:
    """Async client for the Jupiter DEX aggregator API (v6)."""

    def __init__(self, config: JupiterConfig) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Create the HTTP session."""
        timeout = aiohttp.ClientTimeout(total=self._config.request_timeout_seconds)
        self._session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("JupiterClient session not started — call start() first")
        return self._session

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int | None = None,
    ) -> dict[str, Any]:
        """Fetch a swap quote from Jupiter Quote API v6.

        Args:
            input_mint: Input token mint address.
            output_mint: Output token mint address.
            amount: Amount in smallest token unit (lamports for SOL).
            slippage_bps: Slippage tolerance in basis points.

        Returns:
            Quote response dictionary from Jupiter.
        """
        session = self._ensure_session()
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": slippage_bps or self._config.default_slippage_bps,
        }
        url = f"{self._config.base_url}/quote"
        log.debug("Fetching quote: {} -> {} amount={}", input_mint[:8], output_mint[:8], amount)
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
            return data

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def get_swap_transaction(
        self,
        quote_response: dict[str, Any],
        user_public_key: str,
    ) -> dict[str, Any]:
        """Request a swap transaction from Jupiter Swap API.

        Args:
            quote_response: Quote response from get_quote().
            user_public_key: The user's Solana wallet public key.

        Returns:
            Swap transaction response containing the serialized transaction.
        """
        session = self._ensure_session()
        url = f"{self._config.base_url}/swap"
        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True,
        }
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
            return data

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def get_token_price(self, token_address: str) -> float:
        """Fetch the USD price of a token from Jupiter Price API.

        Args:
            token_address: Token mint address.

        Returns:
            Token price in USD.
        """
        session = self._ensure_session()
        url = self._config.price_api_url
        params = {"ids": token_address}
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            price_data = data.get("data", {}).get(token_address, {})
            return float(price_data.get("price", 0.0))

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    async def get_routes(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
    ) -> list[dict[str, Any]]:
        """Fetch multiple swap routes for arbitrage comparison.

        Args:
            input_mint: Input token mint address.
            output_mint: Output token mint address.
            amount: Amount in smallest token unit.

        Returns:
            List of route dictionaries from Jupiter.
        """
        session = self._ensure_session()
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": self._config.default_slippage_bps,
        }
        url = f"{self._config.base_url}/quote"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            routes: list[dict[str, Any]] = data.get("routePlan", [])
            return routes
