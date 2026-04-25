"""Anti-rug engine — orchestrates all safety checks before trading."""

from __future__ import annotations

from typing import Any

from core.logger import LoggerFactory
from safety.honeypot_detector import HoneypotDetector
from safety.token_validator import TokenValidator

log = LoggerFactory.get_logger("anti_rug")


class AntiRugEngine:
    """Orchestrates all safety checks for a token.

    Combines TokenValidator checks, HoneypotDetector, and liquidity lock
    verification into a single validate_token() call.
    """

    def __init__(
        self,
        token_validator: TokenValidator,
        honeypot_detector: HoneypotDetector,
    ) -> None:
        self._token_validator = token_validator
        self._honeypot_detector = honeypot_detector

    async def validate_token(self, token_address: str) -> tuple[bool, dict[str, Any]]:
        """Run the full safety validation pipeline.

        Args:
            token_address: Token mint address.

        Returns:
            Tuple of (is_safe, details_dict) where details contains
            the results of each individual check.
        """
        details: dict[str, Any] = {}

        validator_ok, validator_details = await self._token_validator.validate(token_address)
        details["validator"] = validator_details

        is_honeypot = await self._honeypot_detector.is_honeypot(token_address)
        details["honeypot"] = is_honeypot

        is_safe = validator_ok and not is_honeypot
        details["overall_safe"] = is_safe

        if not is_safe:
            log.warning("Token {} failed safety checks: {}", token_address[:8], details)

        return is_safe, details
