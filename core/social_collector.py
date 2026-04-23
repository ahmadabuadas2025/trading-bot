"""Real social-data collection layer.

The LLM does not browse the web. Before each LLM scan we gather
per-coin social data from free sources and cache it in SQLite. This
cached blob is then fed into the LLM prompt.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from clients.coingecko import CoinGeckoClient
from clients.dexscreener import DexScreenerClient
from core.db import Database
from core.http import HttpClient
from core.time_utils import TimeProvider


@dataclass
class SocialData:
    """Normalised social blob persisted per coin.

    Attributes:
        coin_address: Mint address.
        coin_symbol: Ticker.
        reddit_posts: Count of matching Reddit posts in the last 24h.
        reddit_upvotes: Average upvotes across matching posts.
        reddit_top_comment: Top comment text snippet.
        has_twitter: Whether DexScreener lists a Twitter/X link.
        twitter_followers: Follower count where known.
        has_telegram: Whether DexScreener lists a Telegram link.
        telegram_members: Member count where known.
        has_website: Whether DexScreener lists a website.
        social_links: Total social-link count.
        coingecko_score: CoinGecko community score.
        lunar_volume: LunarCrush social volume.
        data_quality: ``full``/``partial``/``none``.
    """

    coin_address: str
    coin_symbol: str
    reddit_posts: int = 0
    reddit_upvotes: float = 0.0
    reddit_top_comment: str | None = None
    has_twitter: bool = False
    twitter_followers: int = 0
    has_telegram: bool = False
    telegram_members: int = 0
    has_website: bool = False
    social_links: int = 0
    coingecko_score: float | None = None
    lunar_volume: float | None = None
    data_quality: str = "none"


class SocialCollector:
    """Gather social signals from Reddit, DexScreener, CoinGecko, LunarCrush."""

    REDDIT_URL: str = "https://www.reddit.com/r/{sub}/search.json"
    LUNAR_URL: str = "https://lunarcrush.com/api4/public/coins/{symbol}/v1"

    def __init__(
        self,
        http: HttpClient,
        db: Database,
        dex: DexScreenerClient,
        coingecko: CoinGeckoClient,
        config: dict[str, Any],
        lunarcrush_api_key: str | None = None,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Create the collector.

        Args:
            http: Shared HTTP client.
            db: Connected :class:`Database`.
            dex: DexScreener client for social-links lookup.
            coingecko: CoinGecko client for community metrics.
            config: The ``social_collector`` config section.
            lunarcrush_api_key: Optional LunarCrush key.
            time_provider: Optional injected clock.
        """
        self._http = http
        self._db = db
        self._dex = dex
        self._cg = coingecko
        self._cfg = config
        self._lunar_key = lunarcrush_api_key
        self._time = time_provider or TimeProvider()
        self._ttl_hours = float(config.get("cache_ttl_hours", 6))
        self._subs = list(config.get("reddit_subreddits", ["CryptoMoonShots", "solana"]))
        self._gap = float(config.get("reddit_request_gap_seconds", 2.0))

    async def get_cached(self, coin_address: str) -> dict[str, Any] | None:
        """Return cached social data if still fresh.

        Args:
            coin_address: Solana mint address.

        Returns:
            A row dict from ``social_data_cache`` or ``None``.
        """
        now_iso = self._time.now_iso()
        return await self._db.fetchone(
            "SELECT * FROM social_data_cache WHERE coin_address = ? "
            "AND expires_at > ? ORDER BY collected_at DESC LIMIT 1",
            (coin_address, now_iso),
        )

    async def _reddit_counts(self, symbol: str) -> tuple[int, float, str | None]:
        """Query Reddit for posts mentioning a symbol.

        Args:
            symbol: Ticker to search for.

        Returns:
            Tuple of ``(post_count, avg_upvotes, top_comment)``.
        """
        total_posts = 0
        total_ups: list[float] = []
        top_comment: str | None = None
        for sub in self._subs:
            url = self.REDDIT_URL.format(sub=sub)
            try:
                data = await self._http.request_json(
                    "GET", url,
                    params={"q": symbol, "sort": "new", "t": "day", "limit": 10},
                    headers={"User-Agent": "SolanaMemBot/0.1 (Reddit read-only)"},
                )
            except Exception:  # noqa: BLE001
                continue
            children = (data or {}).get("data", {}).get("children") or []
            for ch in children:
                post = (ch or {}).get("data") or {}
                total_posts += 1
                total_ups.append(float(post.get("ups") or 0))
                if top_comment is None and post.get("selftext"):
                    top_comment = str(post["selftext"])[:200]
            await asyncio.sleep(self._gap)
        avg = sum(total_ups) / len(total_ups) if total_ups else 0.0
        return total_posts, avg, top_comment

    async def _lunar(self, symbol: str) -> float | None:
        """Query LunarCrush social volume.

        Args:
            symbol: Ticker to look up.

        Returns:
            Social volume or ``None`` when unavailable.
        """
        if not self._lunar_key:
            return None
        url = self.LUNAR_URL.format(symbol=symbol.upper())
        try:
            data = await self._http.request_json(
                "GET", url, headers={"Authorization": f"Bearer {self._lunar_key}"}
            )
        except Exception:  # noqa: BLE001
            return None
        payload = (data or {}).get("data") or {}
        val = payload.get("social_volume") or payload.get("socialVolume")
        return float(val) if val is not None else None

    async def collect(self, coin_address: str, coin_symbol: str) -> SocialData:
        """Collect social data and persist it to the cache.

        Args:
            coin_address: Solana mint address.
            coin_symbol: Ticker.

        Returns:
            A fully populated :class:`SocialData` instance.
        """
        cached = await self.get_cached(coin_address)
        if cached is not None:
            return SocialData(
                coin_address=coin_address,
                coin_symbol=coin_symbol,
                reddit_posts=int(cached.get("reddit_posts") or 0),
                reddit_upvotes=float(cached.get("reddit_upvotes") or 0.0),
                reddit_top_comment=cached.get("reddit_top_comment"),
                has_twitter=bool(cached.get("has_twitter")),
                twitter_followers=int(cached.get("twitter_followers") or 0),
                has_telegram=bool(cached.get("has_telegram")),
                telegram_members=int(cached.get("telegram_members") or 0),
                has_website=bool(cached.get("has_website")),
                social_links=int(cached.get("social_links") or 0),
                coingecko_score=cached.get("coingecko_score"),
                lunar_volume=cached.get("lunar_volume"),
                data_quality=str(cached.get("data_quality") or "none"),
            )

        dex_detail: dict[str, Any] | None = None
        try:
            dex_detail = await self._dex.token_detail(coin_address)
        except Exception:  # noqa: BLE001
            dex_detail = None

        info = (dex_detail or {}).get("info") or {}
        socials = info.get("socials") or []
        has_twitter = any((s.get("type") or "").lower() in {"twitter", "x"} for s in socials)
        has_telegram = any((s.get("type") or "").lower() == "telegram" for s in socials)
        has_website = bool(info.get("websites")) or any(
            (s.get("type") or "").lower() == "website" for s in socials
        )

        reddit_posts, reddit_ups, top_comment = await self._reddit_counts(coin_symbol)

        cg = None
        if self._cfg.get("coingecko_enabled", True):
            try:
                cg = await self._cg.community_by_contract(coin_address)
            except Exception:  # noqa: BLE001
                cg = None

        lunar = None
        if self._cfg.get("lunarcrush_enabled", True) and self._lunar_key:
            lunar = await self._lunar(coin_symbol)

        quality = "none"
        if cg or lunar or reddit_posts > 0 or socials:
            quality = "partial"
        if cg and reddit_posts > 0 and socials:
            quality = "full"

        sd = SocialData(
            coin_address=coin_address,
            coin_symbol=coin_symbol,
            reddit_posts=reddit_posts,
            reddit_upvotes=reddit_ups,
            reddit_top_comment=top_comment,
            has_twitter=has_twitter,
            twitter_followers=int((cg or {}).get("twitter_followers") or 0),
            has_telegram=has_telegram,
            telegram_members=int((cg or {}).get("telegram_channel_user_count") or 0),
            has_website=has_website,
            social_links=len(socials),
            coingecko_score=(cg or {}).get("community_score"),
            lunar_volume=lunar,
            data_quality=quality,
        )
        await self._store(sd)
        return sd

    async def _store(self, sd: SocialData) -> None:
        """Persist a :class:`SocialData` row with an expiry.

        Args:
            sd: The collected social data.
        """
        expires_at = self._time.add(self._ttl_hours * 3600).isoformat()
        await self._db.execute(
            "INSERT INTO social_data_cache "
            "(coin_address, coin_symbol, reddit_posts, reddit_upvotes, "
            "reddit_top_comment, has_twitter, twitter_followers, has_telegram, "
            "telegram_members, has_website, social_links, coingecko_score, "
            "lunar_volume, data_quality, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sd.coin_address,
                sd.coin_symbol,
                sd.reddit_posts,
                sd.reddit_upvotes,
                sd.reddit_top_comment,
                1 if sd.has_twitter else 0,
                sd.twitter_followers,
                1 if sd.has_telegram else 0,
                sd.telegram_members,
                1 if sd.has_website else 0,
                sd.social_links,
                sd.coingecko_score,
                sd.lunar_volume,
                sd.data_quality,
                expires_at,
            ),
        )

    def to_prompt_blob(self, sd: SocialData) -> dict[str, Any]:
        """Render a social blob in the structure the LLM prompt expects.

        Args:
            sd: The collected social data.

        Returns:
            A JSON-serialisable dict.
        """
        return json.loads(
            json.dumps(
                {
                    "address": sd.coin_address,
                    "symbol": sd.coin_symbol,
                    "reddit_posts_24h": sd.reddit_posts,
                    "reddit_avg_upvotes": sd.reddit_upvotes,
                    "reddit_top_comment": sd.reddit_top_comment,
                    "has_twitter": sd.has_twitter,
                    "twitter_followers": sd.twitter_followers,
                    "has_telegram": sd.has_telegram,
                    "telegram_members": sd.telegram_members,
                    "has_website": sd.has_website,
                    "social_links_count": sd.social_links,
                    "coingecko_community_score": sd.coingecko_score,
                    "lunar_social_volume": sd.lunar_volume,
                    "data_quality": sd.data_quality,
                }
            )
        )
