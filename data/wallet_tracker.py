"""Smart money wallet tracking for copy trading."""

from __future__ import annotations

import os
from typing import Any

import aiohttp

from core.logger import LoggerFactory

log = LoggerFactory.get_logger("wallet_tracker")


class WalletTracker:
    """Track configurable list of smart money wallet addresses.

    Polls recent transactions via Helius or Solana RPC to detect buy/sell
    actions on specific tokens.
    """

    def __init__(self, tracked_wallets: list[str] | None = None) -> None:
        self._tracked_wallets = tracked_wallets or []
        self._session: aiohttp.ClientSession | None = None
        self._helius_api_key: str = os.getenv("HELIUS_API_KEY", "")
        self._wallet_stats: dict[str, dict[str, int]] = {}

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
            raise RuntimeError("WalletTracker session not started")
        return self._session

    @property
    def _rpc_url(self) -> str:
        if self._helius_api_key:
            return f"https://mainnet.helius-rpc.com/?api-key={self._helius_api_key}"
        return "https://api.mainnet-beta.solana.com"

    async def get_recent_trades(self, wallet: str, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent transactions for a wallet.

        Args:
            wallet: Solana wallet address.
            limit: Maximum number of transactions to fetch.

        Returns:
            List of parsed trade dictionaries.
        """
        trades: list[dict[str, Any]] = []
        try:
            if self._helius_api_key:
                trades = await self._fetch_helius_transactions(wallet, limit)
            else:
                trades = await self._fetch_rpc_transactions(wallet, limit)
        except Exception:
            log.warning("Failed to fetch trades for wallet {}", wallet[:8])
        return trades

    async def _fetch_helius_transactions(
        self, wallet: str, limit: int
    ) -> list[dict[str, Any]]:
        """Fetch parsed transactions via Helius enhanced API."""
        session = self._ensure_session()
        url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions"
        params = {"api-key": self._helius_api_key, "limit": limit}
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []
            data: list[dict[str, Any]] = await resp.json()
            return self._parse_helius_transactions(data)

    async def _fetch_rpc_transactions(
        self, wallet: str, limit: int
    ) -> list[dict[str, Any]]:
        """Fetch transactions via standard Solana RPC."""
        session = self._ensure_session()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet, {"limit": limit}],
        }
        async with session.post(self._rpc_url, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            sigs = data.get("result", [])
            return [
                {"signature": s.get("signature", ""), "block_time": s.get("blockTime")}
                for s in sigs
            ]

    def _parse_helius_transactions(
        self, transactions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Parse Helius enhanced transaction data into trade records."""
        trades: list[dict[str, Any]] = []
        for tx in transactions:
            tx_type = tx.get("type", "")
            if tx_type in ("SWAP", "TOKEN_MINT"):
                token_transfers = tx.get("tokenTransfers", [])
                for transfer in token_transfers:
                    trades.append(
                        {
                            "signature": tx.get("signature", ""),
                            "type": tx_type,
                            "token_address": transfer.get("mint", ""),
                            "amount": transfer.get("tokenAmount", 0),
                            "from": transfer.get("fromUserAccount", ""),
                            "to": transfer.get("toUserAccount", ""),
                            "timestamp": tx.get("timestamp"),
                        }
                    )
        return trades

    def is_profitable_wallet(self, wallet: str, min_win_rate: float = 0.55) -> bool:
        """Check if a wallet meets the minimum win rate threshold.

        Args:
            wallet: Wallet address.
            min_win_rate: Minimum required win rate.

        Returns:
            True if the wallet is considered profitable.
        """
        stats = self._wallet_stats.get(wallet, {})
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total = wins + losses
        if total < 5:
            # Not enough data — assume profitable (will be validated over time)
            return True
        return (wins / total) >= min_win_rate

    def update_wallet_stats(self, wallet: str, won: bool) -> None:
        """Update win/loss stats for a tracked wallet."""
        if wallet not in self._wallet_stats:
            self._wallet_stats[wallet] = {"wins": 0, "losses": 0}
        if won:
            self._wallet_stats[wallet]["wins"] += 1
        else:
            self._wallet_stats[wallet]["losses"] += 1

    @property
    def tracked_wallets(self) -> list[str]:
        """Return the list of tracked wallet addresses."""
        return list(self._tracked_wallets)
