"""Basic news/sentiment hooks for token analysis."""

from __future__ import annotations

import os
from typing import Any

import aiohttp

from core.logger import LoggerFactory

log = LoggerFactory.get_logger("sentiment")


class SentimentScanner:
    """Placeholder for external sentiment APIs (LunarCrush, Reddit, Twitter).

    Provides a unified interface for fetching sentiment scores. External API
    integrations can be added by implementing the corresponding methods.
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._lunarcrush_api_key: str = os.getenv("LUNARCRUSH_API_KEY", "")

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
            raise RuntimeError("SentimentScanner session not started")
        return self._session

    async def get_sentiment_score(self, token_symbol: str) -> float | None:
        """Fetch a sentiment score for a token.

        Args:
            token_symbol: Token symbol (e.g., "SOL", "BONK").

        Returns:
            Sentiment score (0.0 to 1.0) or None if unavailable.
        """
        if self._lunarcrush_api_key:
            return await self._fetch_lunarcrush_sentiment(token_symbol)
        log.debug("No sentiment API configured — skipping for {}", token_symbol)
        return None

    async def _fetch_lunarcrush_sentiment(self, token_symbol: str) -> float | None:
        """Fetch sentiment from LunarCrush API."""
        session = self._ensure_session()
        url = "https://lunarcrush.com/api4/public/coins/list/v2"
        headers = {"Authorization": f"Bearer {self._lunarcrush_api_key}"}
        params: dict[str, Any] = {"symbol": token_symbol}
        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                coins = data.get("data", [])
                if coins:
                    galaxy_score = coins[0].get("galaxy_score", 0)
                    return min(1.0, galaxy_score / 100.0)
        except Exception:
            log.warning("LunarCrush request failed for {}", token_symbol)
        return None
