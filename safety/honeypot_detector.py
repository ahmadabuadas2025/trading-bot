"""Honeypot detection by simulating buy/sell via Jupiter quotes."""

from __future__ import annotations

from core.config import SafetyConfig
from core.logger import LoggerFactory
from data.jupiter_client import SOL_MINT, JupiterClient

log = LoggerFactory.get_logger("honeypot")

SIMULATION_AMOUNT_LAMPORTS = 100_000_000  # 0.1 SOL


class HoneypotDetector:
    """Detect honeypot tokens by simulating buy then sell via Jupiter.

    If the sell quote fails or returns significantly less than expected,
    the token is flagged as a honeypot.
    """

    def __init__(self, config: SafetyConfig, jupiter_client: JupiterClient) -> None:
        self._config = config
        self._jupiter = jupiter_client

    async def is_honeypot(self, token_address: str) -> bool:
        """Check if a token is a honeypot.

        Simulates a buy (SOL -> token) then sell (token -> SOL) and checks
        whether the round-trip loses an unreasonable amount.

        Args:
            token_address: Token mint address.

        Returns:
            True if the token appears to be a honeypot.
        """
        if not self._config.honeypot_check_enabled:
            return False

        try:
            buy_quote = await self._jupiter.get_quote(
                input_mint=SOL_MINT,
                output_mint=token_address,
                amount=SIMULATION_AMOUNT_LAMPORTS,
            )

            out_amount = int(buy_quote.get("outAmount", "0"))
            if out_amount <= 0:
                log.warning("Buy quote returned 0 for {} — potential honeypot", token_address[:8])
                return True

            sell_quote = await self._jupiter.get_quote(
                input_mint=token_address,
                output_mint=SOL_MINT,
                amount=out_amount,
            )

            sell_out = int(sell_quote.get("outAmount", "0"))
            if sell_out <= 0:
                log.warning("Sell quote returned 0 for {} — honeypot detected", token_address[:8])
                return True

            round_trip_loss = 1.0 - (sell_out / SIMULATION_AMOUNT_LAMPORTS)
            if round_trip_loss > 0.50:
                log.warning(
                    "Honeypot detected for {}: round-trip loss {:.1%}",
                    token_address[:8],
                    round_trip_loss,
                )
                return True

            return False

        except Exception:
            log.warning("Honeypot check failed for {} — treating as unsafe", token_address[:8])
            return True
