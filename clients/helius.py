"""Helius Enhanced Transactions websocket client (mock-friendly).

The spec calls for a near real-time wallet monitor. We provide a
tiny polling wrapper around Helius' RPC so the rest of the codebase
can consume a simple ``poll`` method. If no key is provided the
caller should fall back to :class:`clients.birdeye.BirdeyeClient` or
the mock provider.
"""

from __future__ import annotations

from typing import Any

from core.http import HttpClient


class HeliusClient:
    """Minimal Helius REST wrapper."""

    def __init__(self, http: HttpClient, api_key: str | None) -> None:
        """Create the client.

        Args:
            http: Shared :class:`HttpClient`.
            api_key: Optional Helius API key.
        """
        self._http = http
        self._api_key = api_key

    @property
    def available(self) -> bool:
        """Whether a Helius key is configured.

        Returns:
            ``True`` if the client can make calls.
        """
        return bool(self._api_key)

    async def wallet_transactions(self, wallet: str, limit: int = 5) -> list[dict[str, Any]]:
        """Fetch recent transactions for a wallet via the REST API.

        Args:
            wallet: Base58 wallet address.
            limit: Max transactions to return.

        Returns:
            List of transaction dicts (empty on failure).
        """
        if not self._api_key:
            return []
        url = (
            f"https://api.helius.xyz/v0/addresses/{wallet}/transactions"
            f"?api-key={self._api_key}&limit={limit}"
        )
        try:
            data = await self._http.request_json("GET", url)
        except Exception:  # noqa: BLE001
            return []
        return data or []
