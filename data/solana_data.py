"""Solana token data feeds via public RPCs or Helius."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import aiohttp

from core.logger import LoggerFactory
from core.models import TokenInfo

log = LoggerFactory.get_logger("solana_data")

SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"


class SolanaDataFeed:
    """Fetch token metadata, holder counts, and age from Solana."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._helius_api_key: str = os.getenv("HELIUS_API_KEY", "")

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
            raise RuntimeError("SolanaDataFeed session not started")
        return self._session

    @property
    def _rpc_url(self) -> str:
        if self._helius_api_key:
            return f"https://mainnet.helius-rpc.com/?api-key={self._helius_api_key}"
        return SOLANA_RPC_URL

    async def _rpc_call(self, method: str, params: list[Any]) -> dict[str, Any]:
        """Make a JSON-RPC call to the Solana RPC endpoint."""
        session = self._ensure_session()
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with session.post(self._rpc_url, json=payload) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
            return data

    async def get_token_info(self, token_address: str) -> TokenInfo:
        """Fetch token metadata from Solana RPC.

        Args:
            token_address: Token mint address.

        Returns:
            TokenInfo with available metadata populated.
        """
        info = TokenInfo(address=token_address)
        try:
            result = await self._rpc_call(
                "getAccountInfo",
                [token_address, {"encoding": "jsonParsed"}],
            )
            account = result.get("result", {}).get("value")
            if account:
                parsed = account.get("data", {}).get("parsed", {}).get("info", {})
                info.decimals = parsed.get("decimals", 9)
                supply_raw = parsed.get("supply", "0")
                info.supply = int(supply_raw) / (10 ** info.decimals) if supply_raw else 0.0
        except Exception:
            log.warning("Failed to fetch token info for {}", token_address)
        return info

    async def get_token_holders(self, token_address: str) -> int:
        """Fetch the number of token holder accounts.

        Args:
            token_address: Token mint address.

        Returns:
            Number of holder accounts, or 0 if unavailable.
        """
        try:
            if self._helius_api_key:
                session = self._ensure_session()
                url = f"https://api.helius.xyz/v0/token-metadata?api-key={self._helius_api_key}"
                async with session.post(url, json={"mintAccounts": [token_address]}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and len(data) > 0:
                            return data[0].get("onChainData", {}).get("holderCount", 0)
            result = await self._rpc_call(
                "getProgramAccounts",
                [
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    {
                        "filters": [
                            {"dataSize": 165},
                            {"memcmp": {"offset": 0, "bytes": token_address}},
                        ],
                        "encoding": "base64",
                    },
                ],
            )
            accounts = result.get("result", [])
            return len(accounts)
        except Exception:
            log.warning("Failed to get holders for {}", token_address)
            return 0

    async def get_token_age_seconds(self, token_address: str) -> float | None:
        """Estimate token age by looking up the first transaction signature.

        Returns:
            Age in seconds, or None if unavailable.
        """
        try:
            result = await self._rpc_call(
                "getSignaturesForAddress",
                [token_address, {"limit": 1}],
            )
            sigs = result.get("result", [])
            if sigs:
                block_time = sigs[0].get("blockTime")
                if block_time:
                    created = datetime.fromtimestamp(block_time, tz=UTC)
                    age = (datetime.now(UTC) - created).total_seconds()
                    return age
        except Exception:
            log.warning("Failed to get token age for {}", token_address)
        return None
