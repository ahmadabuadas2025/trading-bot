"""Math-layer scoring engine with two weight profiles.

Profile A: LLM data is available for the coin.
Profile B: no LLM data (math-only fallback between scans).

All inputs are already-normalised sub-scores; the engine only blends
weights and applies the relevant threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ScoreInputs:
    """Normalised sub-scores for one coin.

    Attributes:
        social: Community-strength score (0-100).
        wallet: Smart-money score (0-100).
        momentum: Price+volume momentum score (0-100).
        safety: Safety metrics score (0-100).
        acceleration: Short-term acceleration score (0-150).
    """

    social: float = 0.0
    wallet: float = 0.0
    momentum: float = 0.0
    safety: float = 0.0
    acceleration: float = 0.0


@dataclass
class ScoreResult:
    """Output of :meth:`ScoringEngine.score`.

    Attributes:
        profile: ``'A'`` or ``'B'``.
        final: Blended final score.
        threshold: Threshold for the selected profile.
        passed: Whether ``final`` exceeds the threshold.
        inputs: Echo of the inputs used.
        weights: Weights applied.
    """

    profile: str
    final: float
    threshold: float
    passed: bool
    inputs: ScoreInputs
    weights: dict[str, float]


class ScoringEngine:
    """Blend sub-scores into a single number with profile-aware weights."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Create the engine.

        Args:
            config: The ``scoring`` section from ``config.yaml``.
        """
        self._a_weights: dict[str, float] = dict(config.get("profile_a_weights", {}))
        self._a_threshold: float = float(config.get("profile_a_entry_threshold", 200))
        self._b_weights: dict[str, float] = dict(config.get("profile_b_weights", {}))
        self._b_threshold: float = float(config.get("profile_b_entry_threshold", 65))
        self._low_wr = float(config.get("auto_tune_low_win_rate", 0.40))
        self._high_wr = float(config.get("auto_tune_high_win_rate", 0.70))
        self._step = float(config.get("auto_tune_step", 0.05))

    @staticmethod
    def momentum_score(price_change_pct: float, vol_liq_ratio: float) -> float:
        """Compute a standard momentum sub-score.

        Args:
            price_change_pct: 1-hour price change fraction.
            vol_liq_ratio: Volume-to-liquidity ratio.

        Returns:
            Momentum score (cap ~90).
        """
        return float(price_change_pct * 250.0 + min(vol_liq_ratio * 12.0, 45.0))

    @staticmethod
    def safety_score(liquidity_usd: float, vol_liq_ratio: float) -> float:
        """Compute a standard safety sub-score.

        Args:
            liquidity_usd: Pool liquidity in USD.
            vol_liq_ratio: Volume-to-liquidity ratio.

        Returns:
            Safety score.
        """
        score = 30.0
        if liquidity_usd > 100000:
            score += 20.0
        if vol_liq_ratio > 5.0:
            score += 15.0
        return score

    @staticmethod
    def acceleration_score(
        vol_5m_usd: float,
        vol_1h_usd: float,
        price_change_1h_pct: float,
        vol_liq_ratio: float,
    ) -> float:
        """Short-term acceleration sub-score, cap 150.

        Args:
            vol_5m_usd: 5-minute volume in USD.
            vol_1h_usd: 1-hour volume in USD.
            price_change_1h_pct: 1-hour price change fraction.
            vol_liq_ratio: Volume-to-liquidity ratio.

        Returns:
            Acceleration score.
        """
        score = 0.0
        expected_per_5m = (vol_1h_usd / 12.0) if vol_1h_usd > 0 else 0.0
        if vol_5m_usd > 1.5 * expected_per_5m and expected_per_5m > 0:
            score += 40.0
        if 0.0 <= price_change_1h_pct <= 0.08:
            score += 30.0
        if vol_liq_ratio > 3.0:
            score += 30.0
        return min(score, 150.0)

    def score(self, inputs: ScoreInputs, have_llm_data: bool) -> ScoreResult:
        """Blend sub-scores using the appropriate profile.

        Args:
            inputs: Normalised sub-scores.
            have_llm_data: True if a valid LLM result exists for the coin.

        Returns:
            A :class:`ScoreResult`.
        """
        weights = self._a_weights if have_llm_data else self._b_weights
        threshold = self._a_threshold if have_llm_data else self._b_threshold
        final = (
            inputs.social * weights.get("social", 0.0)
            + inputs.wallet * weights.get("wallet", 0.0)
            + inputs.momentum * weights.get("momentum", 0.0)
            + inputs.safety * weights.get("safety", 0.0)
            + inputs.acceleration * weights.get("acceleration", 0.0)
        )
        # Profile A scales up (weights 0.15-0.25, sub-scores up to ~150)
        # so we multiply by 10 to land in the 0-300+ range the spec
        # threshold of 200 expects. Profile B leaves it alone.
        if have_llm_data:
            final *= 10.0
        return ScoreResult(
            profile="A" if have_llm_data else "B",
            final=final,
            threshold=threshold,
            passed=final >= threshold,
            inputs=inputs,
            weights=dict(weights),
        )

    def tune_profile_a(self, win_rate_by_source: dict[str, float]) -> dict[str, float]:
        """Adjust Profile A weights based on per-source win rates.

        Args:
            win_rate_by_source: Win-rate per weight key (0-1).

        Returns:
            A new weights dict summing to 1.0.
        """
        new_weights = dict(self._a_weights)
        for key, wr in win_rate_by_source.items():
            if key not in new_weights:
                continue
            if wr < self._low_wr:
                new_weights[key] = max(new_weights[key] - self._step, 0.0)
            elif wr > self._high_wr:
                new_weights[key] = new_weights[key] + self._step
        total = sum(new_weights.values())
        if total == 0:
            return self._a_weights
        return {k: v / total for k, v in new_weights.items()}
