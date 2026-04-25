"""Market liquidity monitoring for tracked tokens."""

from __future__ import annotations

import os
from typing import Any

import aiohttp

from core.logger import LoggerFactory

log = LoggerFactory.get_logger("liquidity")


class LiquidityTracker:
    """Monitor liquidity pools for tracked tokens and detect spikes."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._birdeye_api_key: str = os.getenv("BIRDEYE_API_KEY", "")
        self._liquidity_cache: dict[str, float] = {}
        self._previous_liquidity: dict[str, float] = {}

    async def start(self) -> None:
        """Create the HTTP session."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("LiquidityTracker session not started")
        return self._session

    async def get_liquidity(self, token_address: str) -> float:
        """Fetch the current liquidity in USD for a token.

        Args:
            token_address: Token mint address.

        Returns:
            Liquidity value in USD.
        """
        try:
            if self._birdeye_api_key:
                return await self._fetch_birdeye_liquidity(token_address)
            return self._liquidity_cache.get(token_address, 0.0)
        except Exception:
            log.warning("Failed to fetch liquidity for {}", token_address[:8])
            return self._liquidity_cache.get(token_address, 0.0)

    async def _fetch_birdeye_liquidity(self, token_address: str) -> float:
        """Fetch liquidity data from Birdeye API."""
        session = self._ensure_session()
        url = f"https://public-api.birdeye.so/defi/token_overview?address={token_address}"
        headers = {"X-API-KEY": self._birdeye_api_key}
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return 0.0
            data: dict[str, Any] = await resp.json()
            liquidity = float(data.get("data", {}).get("liquidity", 0.0))
            self._update_cache(token_address, liquidity)
            return liquidity

    def _update_cache(self, token_address: str, liquidity: float) -> None:
        """Update liquidity cache and track previous values for spike detection."""
        old = self._liquidity_cache.get(token_address, 0.0)
        self._previous_liquidity[token_address] = old
        self._liquidity_cache[token_address] = liquidity

    def detect_liquidity_spike(
        self, token_address: str, threshold_multiplier: float = 2.0
    ) -> bool:
        """Detect a sudden liquidity inflow for a token.

        Args:
            token_address: Token mint address.
            threshold_multiplier: Multiplier above previous liquidity to flag as spike.

        Returns:
            True if a liquidity spike is detected.
        """
        current = self._liquidity_cache.get(token_address, 0.0)
        previous = self._previous_liquidity.get(token_address, 0.0)
        if previous <= 0:
            return False
        return current >= previous * threshold_multiplier

    def set_liquidity(self, token_address: str, liquidity: float) -> None:
        """Manually set liquidity for a token (useful for testing / paper mode)."""
        self._update_cache(token_address, liquidity)
