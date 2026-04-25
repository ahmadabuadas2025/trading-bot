"""Risk scoring system for tokens — score <= 50 to trade."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from core.config import SafetyConfig
from core.database import Database
from core.logger import LoggerFactory
from core.models import RiskScore
from data.liquidity_tracker import LiquidityTracker
from data.solana_data import SolanaDataFeed
from safety.honeypot_detector import HoneypotDetector
from safety.token_validator import TokenValidator

log = LoggerFactory.get_logger("risk_scorer")

DEFAULT_WEIGHTS: dict[str, float] = {
    "mint_authority": 20.0,
    "freeze_authority": 20.0,
    "holder_concentration": 15.0,
    "token_age": 15.0,
    "honeypot": 25.0,
    "liquidity": 5.0,
}


class RiskScorer:
    """Compute a 0–100 risk score for tokens based on all safety checks.

    Lower score = safer. Only trade if risk score <= max_risk_score (default 50).
    """

    def __init__(
        self,
        config: SafetyConfig,
        db: Database,
        token_validator: TokenValidator,
        honeypot_detector: HoneypotDetector,
        solana_data: SolanaDataFeed,
        liquidity_tracker: LiquidityTracker,
        weights: dict[str, float] | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._token_validator = token_validator
        self._honeypot_detector = honeypot_detector
        self._solana_data = solana_data
        self._liquidity_tracker = liquidity_tracker
        self._weights = weights or DEFAULT_WEIGHTS

    async def score_token(self, token_address: str) -> RiskScore:
        """Compute a risk score for a token.

        Args:
            token_address: Token mint address.

        Returns:
            RiskScore with score, check details, and safety flag.
        """
        checks: dict[str, Any] = {}
        score = 0.0

        mint_ok = await self._token_validator.check_mint_authority(token_address)
        checks["mint_authority"] = mint_ok
        if not mint_ok:
            score += self._weights.get("mint_authority", 20)

        freeze_ok = await self._token_validator.check_freeze_authority(token_address)
        checks["freeze_authority"] = freeze_ok
        if not freeze_ok:
            score += self._weights.get("freeze_authority", 20)

        holder_ok = await self._token_validator.check_holder_concentration(token_address)
        checks["holder_concentration"] = holder_ok
        if not holder_ok:
            score += self._weights.get("holder_concentration", 15)

        age_ok = await self._token_validator.check_token_age(token_address)
        checks["token_age"] = age_ok
        if not age_ok:
            score += self._weights.get("token_age", 15)

        is_honeypot = await self._honeypot_detector.is_honeypot(token_address)
        checks["honeypot"] = is_honeypot
        if is_honeypot:
            score += self._weights.get("honeypot", 25)

        liquidity = await self._liquidity_tracker.get_liquidity(token_address)
        checks["liquidity_usd"] = liquidity
        if liquidity < 10000:
            score += self._weights.get("liquidity", 5)

        final_score = min(100, int(score))
        is_safe = final_score <= self._config.max_risk_score

        risk_score = RiskScore(
            token_address=token_address,
            score=final_score,
            checks=checks,
            is_safe=is_safe,
            timestamp=datetime.now(UTC),
        )

        await self._save_score(risk_score)
        return risk_score

    async def _save_score(self, risk_score: RiskScore) -> None:
        """Persist the risk score to the database."""
        try:
            await self._db.execute(
                """INSERT OR REPLACE INTO token_risk_scores
                   (token_address, score, checks_json, is_safe, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    risk_score.token_address,
                    risk_score.score,
                    json.dumps(risk_score.checks),
                    1 if risk_score.is_safe else 0,
                    risk_score.timestamp.isoformat(),
                ),
            )
        except Exception:
            log.warning("Failed to save risk score for {}", risk_score.token_address[:8])
