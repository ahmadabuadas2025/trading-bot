"""DexScreener free-API client.

Documented endpoints used:

* ``/latest/dex/search``                  — trending / query search
* ``/token-boosts/top/v1``                — top-boosted tokens
* ``/token-boosts/latest/v1``             — newest boosts
* ``/token-profiles/latest/v1``           — newest listings
* ``/latest/dex/tokens/{address}``        — token detail (includes socials)
"""

from __future__ import annotations

from typing import Any

from core.http import HttpClient


class DexScreenerClient:
    """Thin wrapper around DexScreener endpoints."""

    BASE: str = "https://api.dexscreener.com"

    def __init__(self, http: HttpClient) -> None:
        """Create the client.

        Args:
            http: Shared :class:`HttpClient`.
        """
        self._http = http

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Search the DEX index.

        Args:
            query: Free-text query (e.g. ``"trending"``).

        Returns:
            A list of pair dicts as returned by DexScreener.
        """
        url = f"{self.BASE}/latest/dex/search"
        data = await self._http.request_json("GET", url, params={"q": query})
        pairs = (data or {}).get("pairs") or []
        return [p for p in pairs if p.get("chainId") == "solana"]

    async def top_boosts(self) -> list[dict[str, Any]]:
        """Return the current top-boosted tokens (Solana only).

        Returns:
            List of boost records.
        """
        url = f"{self.BASE}/token-boosts/top/v1"
        data = await self._http.request_json("GET", url)
        return [x for x in (data or []) if x.get("chainId") == "solana"]

    async def latest_boosts(self) -> list[dict[str, Any]]:
        """Return the newest boosted tokens (Solana only).

        Returns:
            List of boost records.
        """
        url = f"{self.BASE}/token-boosts/latest/v1"
        data = await self._http.request_json("GET", url)
        return [x for x in (data or []) if x.get("chainId") == "solana"]

    async def latest_profiles(self) -> list[dict[str, Any]]:
        """Return the newest token profiles (Solana only).

        Returns:
            List of profile records.
        """
        url = f"{self.BASE}/token-profiles/latest/v1"
        data = await self._http.request_json("GET", url)
        return [x for x in (data or []) if x.get("chainId") == "solana"]

    async def token_detail(self, address: str) -> dict[str, Any] | None:
        """Return the top pair + info block for a token address.

        Args:
            address: Solana mint address.

        Returns:
            The first Solana pair dict with an ``info`` block, or ``None``.
        """
        url = f"{self.BASE}/latest/dex/tokens/{address}"
        data = await self._http.request_json("GET", url)
        pairs = (data or {}).get("pairs") or []
        for p in pairs:
            if p.get("chainId") == "solana":
                return p
        return None
