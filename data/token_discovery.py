"""Background worker that discovers new Solana tokens and feeds them to strategies."""

from __future__ import annotations

import asyncio
import os

import aiohttp

from core.logger import LoggerFactory
from data.jupiter_client import JupiterClient
from data.liquidity_tracker import LiquidityTracker
from data.solana_data import SolanaDataFeed
from data.volume_feed import VolumeFeedWorker
from strategies.gem_detector import GemDetectorEngine
from strategies.hot_trading import HotTradingEngine

log = LoggerFactory.get_logger("token_discovery")

# Rate-limit constants
_DEXSCREENER_MIN_INTERVAL = 0.2  # ~300 req/min
_BIRDEYE_MIN_INTERVAL = 1.0  # conservative default


class TokenDiscoveryWorker:
    """Continuously discovers new Solana tokens and feeds them to strategy engines."""

    def __init__(
        self,
        gem_engine: GemDetectorEngine,
        hot_engine: HotTradingEngine,
        jupiter_client: JupiterClient,
        liquidity_tracker: LiquidityTracker,
        solana_data: SolanaDataFeed,
        volume_feed: VolumeFeedWorker | None = None,
        birdeye_api_key: str = "",
        poll_interval: float = 15.0,
    ) -> None:
        self._gem_engine = gem_engine
        self._hot_engine = hot_engine
        self._jupiter = jupiter_client
        self._liquidity = liquidity_tracker
        self._solana_data = solana_data
        self._volume_feed = volume_feed
        self._birdeye_key = birdeye_api_key or os.getenv("BIRDEYE_API_KEY", "")
        self._poll_interval = poll_interval
        self._seen_tokens: set[str] = set()
        self._running = False
        self._session: aiohttp.ClientSession | None = None

    async def run(self) -> None:
        """Main discovery loop — poll for new tokens periodically."""
        self._running = True
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
        )
        log.info("TokenDiscoveryWorker started (interval={}s)", self._poll_interval)

        try:
            while self._running:
                try:
                    tokens = await self._discover_new_tokens()
                    new_count = 0
                    for token in tokens:
                        address = token["address"]
                        if address in self._seen_tokens:
                            continue
                        self._seen_tokens.add(address)
                        new_count += 1

                        self._gem_engine.add_candidate(address)

                        if self._volume_feed is not None:
                            self._volume_feed.track_token(address)

                        volume = token.get("volume", 0.0)
                        if volume > 0:
                            self._hot_engine.record_volume(address, volume)

                        liquidity = token.get("liquidity", 0.0)
                        if liquidity > 0:
                            self._liquidity.set_liquidity(address, liquidity)

                    if new_count > 0:
                        log.info(
                            "Discovered {} new tokens ({} total tracked)",
                            new_count,
                            len(self._seen_tokens),
                        )
                except Exception:
                    log.exception("Error in token discovery cycle")

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

    async def _discover_new_tokens(self) -> list[dict[str, object]]:
        """Discover tokens from multiple free/paid sources."""
        tokens: list[dict[str, object]] = []

        # Primary: DexScreener (free, no API key)
        dex_tokens = await self._fetch_dexscreener_tokens()
        tokens.extend(dex_tokens)

        # Secondary: Birdeye (if API key available)
        if self._birdeye_key:
            birdeye_tokens = await self._fetch_birdeye_tokens()
            tokens.extend(birdeye_tokens)

        return tokens

    # ------------------------------------------------------------------
    # DexScreener (free)
    # ------------------------------------------------------------------

    async def _fetch_dexscreener_tokens(self) -> list[dict[str, object]]:
        """Fetch new Solana token pairs from DexScreener."""
        tokens: list[dict[str, object]] = []
        if not self._session:
            return tokens

        # Endpoint 1: search for recent Solana pairs
        try:
            url = "https://api.dexscreener.com/latest/dex/search?q=sol"
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs") or []
                    for pair in pairs:
                        if pair.get("chainId") != "solana":
                            continue
                        token = self._parse_dexscreener_pair(pair)
                        if token:
                            tokens.append(token)
        except Exception:
            log.warning("DexScreener search request failed")

        await asyncio.sleep(_DEXSCREENER_MIN_INTERVAL)

        # Endpoint 2: latest token profiles
        try:
            url = "https://api.dexscreener.com/token-profiles/latest/v1"
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    profiles = data if isinstance(data, list) else data.get("data", [])
                    for profile in profiles:
                        if profile.get("chainId") != "solana":
                            continue
                        address = profile.get("tokenAddress", "")
                        if address:
                            tokens.append({
                                "address": address,
                                "volume": 0.0,
                                "liquidity": 0.0,
                                "source": "dexscreener_profiles",
                            })
        except Exception:
            log.warning("DexScreener token-profiles request failed")

        return tokens

    @staticmethod
    def _parse_dexscreener_pair(pair: dict) -> dict[str, object] | None:
        """Extract token info from a DexScreener pair object."""
        base_token = pair.get("baseToken", {})
        address = base_token.get("address", "")
        if not address:
            return None

        volume_obj = pair.get("volume") or {}
        volume_5m = float(volume_obj.get("m5", 0) or 0)
        volume_1h = float(volume_obj.get("h1", 0) or 0)

        liquidity_obj = pair.get("liquidity") or {}
        liquidity_usd = float(liquidity_obj.get("usd", 0) or 0)

        return {
            "address": address,
            "volume": volume_5m if volume_5m > 0 else volume_1h,
            "liquidity": liquidity_usd,
            "source": "dexscreener_search",
        }

    # ------------------------------------------------------------------
    # Birdeye (requires API key)
    # ------------------------------------------------------------------

    async def _fetch_birdeye_tokens(self) -> list[dict[str, object]]:
        """Fetch trending tokens from Birdeye API."""
        tokens: list[dict[str, object]] = []
        if not self._session or not self._birdeye_key:
            return tokens

        try:
            url = (
                "https://public-api.birdeye.so/defi/tokenlist"
                "?sort_by=v24hChangePercent&sort_type=desc&offset=0&limit=50"
            )
            headers = {"X-API-KEY": self._birdeye_key}
            async with self._session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("data", {}).get("tokens", [])
                    for item in items:
                        address = item.get("address", "")
                        if not address:
                            continue
                        tokens.append({
                            "address": address,
                            "volume": float(item.get("v24hUSD", 0) or 0),
                            "liquidity": float(item.get("liquidity", 0) or 0),
                            "source": "birdeye",
                        })
        except Exception:
            log.warning("Birdeye token list request failed")

        return tokens
