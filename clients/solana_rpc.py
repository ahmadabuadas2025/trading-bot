"""Solana JSON-RPC client for wallet balance queries."""

from __future__ import annotations

from core.http import HttpClient


class SolanaRPCClient:
    """Async wrapper around the Solana JSON-RPC ``getBalance`` and
    ``getTokenAccountsByOwner`` methods.
    """

    DEFAULT_RPC = "https://api.mainnet-beta.solana.com"

    def __init__(self, http: HttpClient, rpc_url: str | None = None) -> None:
        """Create the client.

        Args:
            http: Shared :class:`HttpClient` with retry/CB support.
            rpc_url: Solana RPC endpoint override.
        """
        self._http = http
        self._rpc_url = rpc_url or self.DEFAULT_RPC

    async def get_sol_balance(self, public_key: str) -> float:
        """Fetch SOL balance (not lamports) for a wallet.

        Args:
            public_key: Base-58 Solana public key.

        Returns:
            SOL balance as a float.
        """
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [public_key],
        }
        resp = await self._http.request_json("POST", self._rpc_url, json_body=body)
        lamports = resp.get("result", {}).get("value", 0)
        return lamports / 1e9

    async def get_token_accounts(self, public_key: str) -> list[dict]:
        """Fetch all SPL token accounts for a wallet.

        Args:
            public_key: Base-58 Solana public key.

        Returns:
            A list of token-account dicts from the RPC response.
        """
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                public_key,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        }
        resp = await self._http.request_json("POST", self._rpc_url, json_body=body)
        return resp.get("result", {}).get("value", [])
