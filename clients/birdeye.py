"""Birdeye API client for wallet activity and OHLCV.

All methods return ``None`` or empty lists when no API key is set so
higher-level services can treat the client as best-effort.
"""

from __future__ import annotations

from typing import Any

from core.http import HttpClient


class BirdeyeClient:
    """Minimal Birdeye client."""

    BASE: str = "https://public-api.birdeye.so"

    def __init__(self, http: HttpClient, api_key: str | None) -> None:
        """Create the client.

        Args:
            http: Shared :class:`HttpClient`.
            api_key: Optional Birdeye API key. Endpoints return empty
                results when missing.
        """
        self._http = http
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        """Return auth headers, empty when no key is set.

        Returns:
            Header mapping.
        """
        return {"X-API-KEY": self._api_key} if self._api_key else {}

    async def wallet_tx_list(self, wallet: str, limit: int = 5) -> list[dict[str, Any]]:
        """Recent swap transactions for a wallet.

        Args:
            wallet: Base58 wallet address.
            limit: Max rows to return.

        Returns:
            List of transaction dicts (empty on failure).
        """
        if not self._api_key:
            return []
        url = f"{self.BASE}/v1/wallet/tx_list"
        params = {"wallet": wallet, "tx_type": "swap", "limit": limit}
        try:
            data = await self._http.request_json(
                "GET", url, params=params, headers=self._headers()
            )
        except Exception:  # noqa: BLE001
            return []
        return ((data or {}).get("data") or {}).get("items") or []

    async def top_active_wallets(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return a candidate pool of active wallets.

        Args:
            limit: Max wallets to return.

        Returns:
            List of wallet dicts (empty on failure).
        """
        if not self._api_key:
            return []
        url = f"{self.BASE}/v1/wallet/list"
        try:
            data = await self._http.request_json(
                "GET", url, params={"limit": limit}, headers=self._headers()
            )
        except Exception:  # noqa: BLE001
            return []
        return ((data or {}).get("data") or {}).get("items") or []
