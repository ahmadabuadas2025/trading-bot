"""Route scanner for Jupiter arbitrage opportunities."""

from __future__ import annotations

from typing import Any

from core.logger import LoggerFactory
from core.models import ArbitrageOpportunity
from data.jupiter_client import JupiterClient

log = LoggerFactory.get_logger("route_scanner")


class RouteScanner:
    """Continuously fetch and compare Jupiter swap routes for arbitrage."""

    def __init__(self, jupiter_client: JupiterClient) -> None:
        self._jupiter = jupiter_client

    async def scan_routes(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
    ) -> list[dict[str, Any]]:
        """Fetch multiple routes from Jupiter for a given token pair.

        Args:
            input_mint: Input token mint address.
            output_mint: Output token mint address.
            amount: Amount in smallest token unit.

        Returns:
            List of route dictionaries.
        """
        try:
            routes = await self._jupiter.get_routes(input_mint, output_mint, amount)
            return routes
        except Exception:
            log.warning("Failed to scan routes for {} -> {}", input_mint[:8], output_mint[:8])
            return []

    async def find_arbitrage_opportunity(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        min_profit_pct: float = 0.003,
    ) -> ArbitrageOpportunity | None:
        """Check for an arbitrage opportunity between two routes.

        Fetches quotes for A->B and B->A, then compares to see if there
        is a profitable round-trip.

        Args:
            input_mint: Token A mint address.
            output_mint: Token B mint address.
            amount: Amount in smallest token unit of input.
            min_profit_pct: Minimum profit threshold as a fraction.

        Returns:
            ArbitrageOpportunity if found, else None.
        """
        try:
            buy_quote = await self._jupiter.get_quote(input_mint, output_mint, amount)
            buy_output = int(buy_quote.get("outAmount", "0"))
            if buy_output <= 0:
                return None

            sell_quote = await self._jupiter.get_quote(output_mint, input_mint, buy_output)
            sell_output = int(sell_quote.get("outAmount", "0"))
            if sell_output <= 0:
                return None

            profit_pct = (sell_output - amount) / amount

            if profit_pct >= min_profit_pct:
                log.info(
                    "Arbitrage found: {} -> {} -> {} profit {:.3%}",
                    input_mint[:8],
                    output_mint[:8],
                    input_mint[:8],
                    profit_pct,
                )
                return ArbitrageOpportunity(
                    input_mint=input_mint,
                    output_mint=output_mint,
                    route_buy=str(buy_quote.get("routePlan", "")),
                    route_sell=str(sell_quote.get("routePlan", "")),
                    buy_amount=float(amount),
                    expected_output_buy=float(buy_output),
                    expected_output_sell=float(sell_output),
                    expected_profit_pct=profit_pct,
                    expected_profit_usd=0.0,
                )

        except Exception:
            log.debug("Arb scan failed for {} <-> {}", input_mint[:8], output_mint[:8])

        return None
