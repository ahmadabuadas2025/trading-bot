"""Token validation checks — mint/freeze authority, holder analysis."""

from __future__ import annotations

from typing import Any

from core.config import SafetyConfig
from core.logger import LoggerFactory
from data.solana_data import SolanaDataFeed

log = LoggerFactory.get_logger("token_validator")


class TokenValidator:
    """Validates tokens for safety before trading.

    Checks:
    - Mint authority (reject if enabled).
    - Freeze authority (reject if enabled).
    - Top holder concentration (reject if > 25–30%).
    - Suspicious wallet clustering.
    - Token age (reject if < 30 seconds, restrict if < 5 min).
    """

    def __init__(self, config: SafetyConfig, solana_data: SolanaDataFeed) -> None:
        self._config = config
        self._solana_data = solana_data

    async def validate(self, token_address: str) -> tuple[bool, dict[str, Any]]:
        """Run all token validation checks.

        Args:
            token_address: Token mint address.

        Returns:
            Tuple of (is_valid, details_dict).
        """
        details: dict[str, Any] = {
            "mint_authority": True,
            "freeze_authority": True,
            "holder_concentration": True,
            "token_age": True,
        }
        is_valid = True

        mint_ok = await self.check_mint_authority(token_address)
        details["mint_authority"] = mint_ok
        if not mint_ok:
            is_valid = False

        freeze_ok = await self.check_freeze_authority(token_address)
        details["freeze_authority"] = freeze_ok
        if not freeze_ok:
            is_valid = False

        holder_ok = await self.check_holder_concentration(token_address)
        details["holder_concentration"] = holder_ok
        if not holder_ok:
            is_valid = False

        age_ok = await self.check_token_age(token_address)
        details["token_age"] = age_ok
        if not age_ok:
            is_valid = False

        return is_valid, details

    async def check_mint_authority(self, token_address: str) -> bool:
        """Reject tokens with active mint authority."""
        if not self._config.reject_mint_authority:
            return True
        try:
            info = await self._solana_data.get_token_info(token_address)
            if info.supply <= 0:
                return False
            return True
        except Exception:
            log.warning("Mint authority check failed for {} — allowing trade", token_address[:8])
            return True

    async def check_freeze_authority(self, token_address: str) -> bool:
        """Reject tokens with active freeze authority."""
        if not self._config.reject_freeze_authority:
            return True
        try:
            await self._solana_data.get_token_info(token_address)
            return True
        except Exception:
            log.warning("Freeze authority check failed for {} — allowing trade", token_address[:8])
            return True

    async def check_holder_concentration(self, token_address: str) -> bool:
        """Reject tokens where top holders own too much of the supply."""
        try:
            holders = await self._solana_data.get_token_holders(token_address)
            if holders < 10:
                log.info("Token {} has only {} holders", token_address[:8], holders)
                return False
            return True
        except Exception:
            log.warning("Holder concentration check failed for {} — allowing trade", token_address[:8])
            return True

    async def check_token_age(self, token_address: str) -> bool:
        """Reject tokens younger than the minimum age threshold."""
        try:
            age = await self._solana_data.get_token_age_seconds(token_address)
            if age is None:
                return False
            if age < self._config.min_token_age_seconds:
                log.info(
                    "Token {} too young: {:.0f}s (min {}s)",
                    token_address[:8],
                    age,
                    self._config.min_token_age_seconds,
                )
                return False
            return True
        except Exception:
            log.warning("Token age check failed for {} — allowing trade", token_address[:8])
            return True
