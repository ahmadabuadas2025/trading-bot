"""Background worker that polls volume data for active tokens."""

from __future__ import annotations

import asyncio
import os

import aiohttp

from core.logger import LoggerFactory
from data.jupiter_client import JupiterClient
from strategies.hot_trading import HotTradingEngine

log = LoggerFactory.get_logger("volume_feed")

# Rate-limit: DexScreener allows ~300 req/min
_DEXSCREENER_MIN_INTERVAL = 0.25


class VolumeFeedWorker:
    """Polls volume data for tracked tokens and feeds it to the hot trading engine."""

    def __init__(
        self,
        hot_engine: HotTradingEngine,
        jupiter_client: JupiterClient,
        birdeye_api_key: str = "",
        poll_interval: float = 5.0,
    ) -> None:
        self._hot_engine = hot_engine
        self._jupiter = jupiter_client
        self._birdeye_key = birdeye_api_key or os.getenv("BIRDEYE_API_KEY", "")
        self._poll_interval = poll_interval
        self._tracked_tokens: set[str] = set()
        self._running = False
        self._session: aiohttp.ClientSession | None = None

    def track_token(self, token_address: str) -> None:
        """Add a token to the volume tracking set."""
        self._tracked_tokens.add(token_address)

    def track_tokens(self, addresses: set[str]) -> None:
        """Bulk-add tokens to the tracking set."""
        self._tracked_tokens.update(addresses)

    async def run(self) -> None:
        """Main loop — poll volume for all tracked tokens periodically."""
        self._running = True
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
        )
        log.info("VolumeFeedWorker started (interval={}s)", self._poll_interval)

        try:
            while self._running:
                try:
                    updated = 0
                    for token in list(self._tracked_tokens):
                        volume = await self._get_volume(token)
                        if volume > 0:
                            self._hot_engine.record_volume(token, volume)
                            updated += 1
                        await asyncio.sleep(_DEXSCREENER_MIN_INTERVAL)

                    if updated > 0:
                        log.debug(
                            "Volume feed: updated {} / {} tokens",
                            updated,
                            len(self._tracked_tokens),
                        )
                except Exception:
                    log.exception("Error in volume feed cycle")

                await asyncio.sleep(self._poll_interval)
        finally:
            await self._close_session()

    def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False

    async def _close_session(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _get_volume(self, token_address: str) -> float:
        """Fetch recent volume for a token using DexScreener or Birdeye."""
        volume = await self._get_dexscreener_volume(token_address)
        if volume > 0:
            return volume

        if self._birdeye_key:
            return await self._get_birdeye_volume(token_address)

        return 0.0

    async def _get_dexscreener_volume(self, token_address: str) -> float:
        """Get 5-minute volume from DexScreener pair data."""
        if not self._session:
            return 0.0
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    return 0.0
                data = await resp.json()
                pairs = data.get("pairs") or []
                if not pairs:
                    return 0.0
                # Use the pair with the highest 5m volume
                best_volume = 0.0
                for pair in pairs:
                    vol_obj = pair.get("volume") or {}
                    vol_5m = float(vol_obj.get("m5", 0) or 0)
                    if vol_5m > best_volume:
                        best_volume = vol_5m
                return best_volume
        except Exception:
            log.debug("DexScreener volume fetch failed for {}", token_address[:8])
            return 0.0

    async def _get_birdeye_volume(self, token_address: str) -> float:
        """Get volume from Birdeye token overview."""
        if not self._session or not self._birdeye_key:
            return 0.0
        try:
            url = f"https://public-api.birdeye.so/defi/token_overview?address={token_address}"
            headers = {"X-API-KEY": self._birdeye_key}
            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return 0.0
                data = await resp.json()
                return float(data.get("data", {}).get("v24hUSD", 0) or 0)
        except Exception:
            log.debug("Birdeye volume fetch failed for {}", token_address[:8])
            return 0.0
