"""Jupiter v6 swap client (live mode only).

The live executor calls :meth:`JupiterClient.quote` and
:meth:`JupiterClient.build_swap_tx` to obtain a signed-transaction
payload. Actually signing and sending the transaction lives in the
executor so this file stays small and testable.
"""

from __future__ import annotations

from typing import Any

from core.http import HttpClient


class JupiterClient:
    """Wrapper over Jupiter's public v6 API."""

    QUOTE_URL: str = "https://quote-api.jup.ag/v6/quote"
    SWAP_URL: str = "https://quote-api.jup.ag/v6/swap"
    WSOL_MINT: str = "So11111111111111111111111111111111111111112"

    def __init__(self, http: HttpClient) -> None:
        """Create the client.

        Args:
            http: Shared :class:`HttpClient`.
        """
        self._http = http

    async def quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int = 100,
    ) -> dict[str, Any]:
        """Fetch a quote route.

        Args:
            input_mint: Mint address of the input token.
            output_mint: Mint address of the output token.
            amount_lamports: Amount in smallest-unit (lamports for SOL).
            slippage_bps: Slippage tolerance in basis points.

        Returns:
            The JSON quote response.
        """
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_lamports,
            "slippageBps": slippage_bps,
        }
        return await self._http.request_json("GET", self.QUOTE_URL, params=params)

    async def build_swap_tx(
        self,
        quote_response: dict[str, Any],
        user_public_key: str,
    ) -> dict[str, Any]:
        """Build an unsigned swap transaction from a quote.

        Args:
            quote_response: The body returned by :meth:`quote`.
            user_public_key: Base58 wallet public key.

        Returns:
            A JSON object with base64-encoded ``swapTransaction``.
        """
        body = {
            "quoteResponse": quote_response,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True,
        }
        return await self._http.request_json("POST", self.SWAP_URL, json_body=body)
