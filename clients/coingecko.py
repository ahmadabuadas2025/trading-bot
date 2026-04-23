"""CoinGecko free-API client (community data + SOL price)."""

from __future__ import annotations

from typing import Any

from core.http import HttpClient


class CoinGeckoClient:
    """Small subset of the CoinGecko v3 API."""

    BASE: str = "https://api.coingecko.com/api/v3"

    def __init__(self, http: HttpClient) -> None:
        """Create the client.

        Args:
            http: Shared :class:`HttpClient`.
        """
        self._http = http

    async def community_by_contract(self, contract_address: str) -> dict[str, Any] | None:
        """Fetch community data for a Solana contract address.

        Args:
            contract_address: Solana mint address.

        Returns:
            A subset of CoinGecko's contract response, or ``None``.
        """
        url = f"{self.BASE}/coins/solana/contract/{contract_address}"
        try:
            data = await self._http.request_json("GET", url)
        except Exception:  # noqa: BLE001
            return None
        if not data or data.get("error"):
            return None
        cd = data.get("community_data") or {}
        links = data.get("links") or {}
        return {
            "twitter_followers": int(cd.get("twitter_followers") or 0),
            "telegram_channel_user_count": int(cd.get("telegram_channel_user_count") or 0),
            "reddit_subscribers": int(cd.get("reddit_subscribers") or 0),
            "community_score": float(data.get("community_score") or 0.0),
            "homepage": (links.get("homepage") or [""])[0],
        }

    async def sol_price_usd(self) -> float | None:
        """Return the current SOL/USD price.

        Returns:
            Price in USD or ``None`` on failure.
        """
        url = f"{self.BASE}/simple/price"
        try:
            data = await self._http.request_json(
                "GET", url, params={"ids": "solana", "vs_currencies": "usd"}
            )
        except Exception:  # noqa: BLE001
            return None
        val = (data or {}).get("solana", {}).get("usd")
        return float(val) if val is not None else None
