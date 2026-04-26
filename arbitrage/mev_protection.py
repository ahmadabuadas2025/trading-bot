"""MEV-aware pre-execution validation and protection."""

from __future__ import annotations

from core.config import ArbitrageConfig
from core.logger import LoggerFactory
from core.models import ArbitrageOpportunity
from data.jupiter_client import JupiterClient
from data.liquidity_tracker import LiquidityTracker

log = LoggerFactory.get_logger("mev_protection")

MIN_LIQUIDITY_THRESHOLD = 10_000.0  # $10K minimum liquidity

KNOWN_LIQUID_TOKENS = {
    "So11111111111111111111111111111111111111112",   # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",   # JUP
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",  # RAY
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
}


class MEVProtection:
    """Re-check prices before execution and protect against MEV attacks.

    - Re-check price immediately before execution.
    - Cancel if price shifts beyond threshold.
    - Avoid low liquidity pools.
    - Prefer stable routing paths.
    """

    def __init__(
        self,
        config: ArbitrageConfig,
        jupiter_client: JupiterClient,
        liquidity_tracker: LiquidityTracker,
    ) -> None:
        self._config = config
        self._jupiter = jupiter_client
        self._liquidity_tracker = liquidity_tracker
        self._price_shift_threshold: float = 0.005  # 0.5% default

    async def validate_pre_execution(self, opportunity: ArbitrageOpportunity) -> bool:
        """Validate an arbitrage opportunity right before execution.

        Re-checks the current price to ensure the opportunity still exists
        and hasn't been front-run.

        Args:
            opportunity: The arbitrage opportunity to validate.

        Returns:
            True if the opportunity is still valid for execution.
        """
        try:
            fresh_quote = await self._jupiter.get_quote(
                input_mint=opportunity.input_mint,
                output_mint=opportunity.output_mint,
                amount=int(opportunity.buy_amount),
            )

            fresh_output = int(fresh_quote.get("outAmount", "0"))
            if fresh_output <= 0:
                log.warning("Pre-execution quote returned 0 — cancelling")
                return False

            original_output = opportunity.expected_output_buy
            if original_output <= 0:
                return False

            price_shift = abs(fresh_output - original_output) / original_output
            if price_shift > self._price_shift_threshold:
                log.warning(
                    "Price shifted {:.3%} > threshold {:.3%} — cancelling",
                    price_shift,
                    self._price_shift_threshold,
                )
                return False

            if not await self.is_safe_route(opportunity):
                return False

            return True

        except Exception:
            log.warning("Pre-execution validation failed — cancelling")
            return False

    async def is_safe_route(self, opportunity: ArbitrageOpportunity) -> bool:
        """Check if the route has sufficient liquidity.

        Args:
            opportunity: The arbitrage opportunity to check.

        Returns:
            True if the route is considered safe.
        """
        if opportunity.output_mint in KNOWN_LIQUID_TOKENS:
            return True
        try:
            liquidity = await self._liquidity_tracker.get_liquidity(opportunity.output_mint)
            if liquidity < MIN_LIQUIDITY_THRESHOLD:
                log.warning(
                    "Low liquidity ${:.0f} for {} — unsafe route",
                    liquidity,
                    opportunity.output_mint[:8],
                )
                return False
            return True
        except Exception:
            log.warning("Liquidity check failed for route safety — allowing known pair")
            return True
