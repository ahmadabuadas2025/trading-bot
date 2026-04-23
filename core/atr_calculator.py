"""ATR(14) calculator with three tiers of data.

1. Primary: Birdeye OHLCV if an API key is set.
2. Fallback: build 5-minute buckets from ``price_ticks``.
3. Last resort: estimate ATR as ``price * volatility_pct``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from core.db import Database
from core.http import HttpClient


@dataclass
class _Candle:
    """One OHLCV candle.

    Attributes:
        high: Candle high.
        low: Candle low.
        close: Candle close.
    """

    high: float
    low: float
    close: float


class ATRCalculator:
    """Compute Average True Range with graceful data degradation."""

    BIRDEYE_URL: str = "https://public-api.birdeye.so/defi/ohlcv"

    def __init__(
        self,
        http: HttpClient,
        db: Database,
        config: dict[str, Any],
        birdeye_api_key: str | None = None,
    ) -> None:
        """Create an ATR calculator.

        Args:
            http: Shared :class:`HttpClient`.
            db: Connected :class:`Database` (for tick fallback).
            config: The ``atr`` section from ``config.yaml``.
            birdeye_api_key: Optional Birdeye key to unlock tier 1.
        """
        self._http = http
        self._db = db
        self._period = int(config.get("period", 14))
        self._bucket_minutes = int(config.get("bucket_minutes", 5))
        self._min_buckets = int(config.get("min_buckets_for_tick_fallback", 10))
        self._fallback_vol = float(config.get("last_resort_volatility_pct", 0.02))
        self._cache_ttl = int(config.get("cache_ttl_seconds", 300))
        self._api_key = birdeye_api_key
        self._cache: dict[str, tuple[float, float]] = {}

    def _cached(self, coin: str) -> float | None:
        """Return a cached ATR if still fresh, else ``None``.

        Args:
            coin: Coin mint address.

        Returns:
            Cached ATR value or ``None``.
        """
        entry = self._cache.get(coin)
        if entry is None:
            return None
        expiry, value = entry
        return value if time.monotonic() < expiry else None

    def _store(self, coin: str, value: float) -> None:
        """Cache an ATR value for ``cache_ttl_seconds`` seconds.

        Args:
            coin: Coin mint address.
            value: ATR value.
        """
        self._cache[coin] = (time.monotonic() + self._cache_ttl, value)

    @staticmethod
    def _atr_from_candles(candles: list[_Candle]) -> float:
        """Standard ATR formula over a list of candles.

        Args:
            candles: Ordered list of :class:`_Candle`, oldest first.

        Returns:
            Mean true range across the period.
        """
        if len(candles) < 2:
            return 0.0
        trs: list[float] = []
        for i in range(1, len(candles)):
            hi, lo, prev_close = candles[i].high, candles[i].low, candles[i - 1].close
            trs.append(max(hi - lo, abs(hi - prev_close), abs(lo - prev_close)))
        if not trs:
            return 0.0
        return sum(trs) / len(trs)

    async def _from_birdeye(self, coin: str) -> float | None:
        """Fetch ATR from Birdeye OHLCV (tier 1).

        Args:
            coin: Coin mint address.

        Returns:
            ATR or ``None`` on any failure.
        """
        if not self._api_key:
            return None
        end = int(time.time())
        start = end - (self._period + 2) * self._bucket_minutes * 60
        params = {
            "address": coin,
            "type": f"{self._bucket_minutes}m",
            "time_from": start,
            "time_to": end,
        }
        try:
            data = await self._http.request_json(
                "GET", self.BIRDEYE_URL, params=params,
                headers={"X-API-KEY": self._api_key},
            )
        except Exception:  # noqa: BLE001
            return None
        items = ((data or {}).get("data") or {}).get("items") or []
        candles = [
            _Candle(high=float(x["h"]), low=float(x["l"]), close=float(x["c"]))
            for x in items[-(self._period + 1):]
        ]
        atr = self._atr_from_candles(candles)
        return atr if atr > 0 else None

    async def _from_ticks(self, coin: str) -> float | None:
        """Build candles from ``price_ticks`` (tier 2).

        Args:
            coin: Coin mint address.

        Returns:
            ATR or ``None`` if not enough ticks.
        """
        window_seconds = (self._period + 1) * self._bucket_minutes * 60
        rows = await self._db.fetchall(
            "SELECT price_usd, ts FROM price_ticks "
            "WHERE coin_address = ? AND ts >= datetime('now', ?) ORDER BY ts ASC",
            (coin, f"-{window_seconds} seconds"),
        )
        if not rows:
            return None
        # Group the sorted ticks into fixed-size windows. Each tick corresponds
        # to roughly one sampling interval; chunk N ticks per candle where N is
        # the bucket granularity in minutes.
        per_candle = max(self._bucket_minutes, 1)
        prices = [float(r["price_usd"]) for r in rows if r.get("price_usd")]
        chunks = [
            prices[i : i + per_candle] for i in range(0, len(prices), per_candle)
        ]
        candles = [
            _Candle(high=max(c), low=min(c), close=c[-1]) for c in chunks if c
        ]
        if len(candles) < self._min_buckets:
            return None
        return self._atr_from_candles(candles[-(self._period + 1):]) or None

    async def compute(self, coin: str, current_price: float) -> float:
        """Return ATR(14) with graceful degradation.

        Args:
            coin: Coin mint address.
            current_price: Latest observed price.

        Returns:
            ATR in price units. Never zero.
        """
        cached = self._cached(coin)
        if cached is not None:
            return cached
        atr = await self._from_birdeye(coin)
        if atr is None:
            atr = await self._from_ticks(coin)
        if atr is None or atr <= 0:
            atr = max(current_price * self._fallback_vol, 1e-9)
        self._store(coin, atr)
        return atr
