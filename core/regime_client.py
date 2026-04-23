"""Market regime detector and position multiplier source.

Queries CoinGecko for BTC + SOL 24h change and Alternative.me for the
Fear and Greed index, classifies the market into ``BULLISH``,
``NEUTRAL`` or ``BEARISH``, and exposes per-bucket multipliers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.db import Database
from core.http import HttpClient


@dataclass
class RegimeSnapshot:
    """Cached regime state.

    Attributes:
        regime: ``'BULLISH'``, ``'NEUTRAL'`` or ``'BEARISH'``.
        btc_change_24h: BTC 24h change as a fraction.
        sol_change_24h: SOL 24h change as a fraction.
        fear_greed: Fear and Greed index (0-100).
    """

    regime: str
    btc_change_24h: float
    sol_change_24h: float
    fear_greed: int


class RegimeClient:
    """Fetch, classify and persist the current market regime."""

    COINGECKO_PRICE: str = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,solana&vs_currencies=usd&include_24hr_change=true"
    )
    FEAR_GREED: str = "https://api.alternative.me/fng/?limit=1"

    def __init__(self, http: HttpClient, db: Database, config: dict[str, Any]) -> None:
        """Create a regime client.

        Args:
            http: Shared :class:`HttpClient`.
            db: Connected :class:`Database` (for persistence).
            config: The ``regime`` section from ``config.yaml``.
        """
        self._http = http
        self._db = db
        self._cfg = config
        self._cached: RegimeSnapshot | None = None

    def _classify(self, btc: float, sol: float, fg: int) -> str:
        """Classify a regime from raw inputs.

        Args:
            btc: BTC 24h change fraction.
            sol: SOL 24h change fraction.
            fg: Fear and Greed index.

        Returns:
            Regime name.
        """
        c = self._cfg
        if (
            btc >= c.get("bullish_btc_threshold", -0.02)
            and sol >= c.get("bullish_sol_threshold", -0.03)
            and fg > c.get("bullish_fg_threshold", 50)
        ):
            return "BULLISH"
        if btc < c.get("bearish_btc_threshold", -0.05) or fg < c.get("bearish_fg_threshold", 35):
            return "BEARISH"
        return "NEUTRAL"

    async def refresh(self) -> RegimeSnapshot:
        """Fetch fresh data, persist it, and return a snapshot.

        Returns:
            The current :class:`RegimeSnapshot`.
        """
        try:
            price = await self._http.request_json("GET", self.COINGECKO_PRICE)
            btc = float(price.get("bitcoin", {}).get("usd_24h_change", 0.0)) / 100.0
            sol = float(price.get("solana", {}).get("usd_24h_change", 0.0)) / 100.0
        except Exception:  # noqa: BLE001
            btc = sol = 0.0
        try:
            fg_payload = await self._http.request_json("GET", self.FEAR_GREED)
            fg = int(fg_payload.get("data", [{}])[0].get("value", 50))
        except Exception:  # noqa: BLE001
            fg = 50
        regime = self._classify(btc, sol, fg)
        snap = RegimeSnapshot(regime=regime, btc_change_24h=btc, sol_change_24h=sol, fear_greed=fg)
        await self._db.execute(
            "INSERT INTO regime_log (regime, btc_change_24h, sol_change_24h, fear_greed) "
            "VALUES (?, ?, ?, ?)",
            (regime, btc, sol, fg),
        )
        self._cached = snap
        return snap

    def current(self) -> RegimeSnapshot:
        """Return the most recent snapshot, defaulting to NEUTRAL.

        Returns:
            The cached :class:`RegimeSnapshot` or a neutral default.
        """
        if self._cached is None:
            return RegimeSnapshot("NEUTRAL", 0.0, 0.0, 50)
        return self._cached

    def get_multiplier(self, bucket_name: str) -> float:
        """Return the position-size multiplier for a bucket.

        Args:
            bucket_name: Bucket key.

        Returns:
            Multiplier in ``[0.0, 1.0]``.
        """
        regime = self.current().regime
        table = self._cfg.get("multipliers", {})
        return float(table.get(regime, {}).get(bucket_name, 0.5))
