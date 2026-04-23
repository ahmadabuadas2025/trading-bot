"""Tests for :mod:`core.scoring_engine`."""

from __future__ import annotations

import pytest

from core.scoring_engine import ScoreInputs, ScoringEngine


def _engine() -> ScoringEngine:
    """Return a scoring engine with the default profile weights.

    Returns:
        A scoring engine instance.
    """
    return ScoringEngine(
        {
            "profile_a_weights": {
                "social": 0.25,
                "wallet": 0.25,
                "momentum": 0.15,
                "safety": 0.20,
                "acceleration": 0.15,
            },
            "profile_a_entry_threshold": 200,
            "profile_b_weights": {
                "social": 0.0,
                "wallet": 0.0,
                "momentum": 0.35,
                "safety": 0.35,
                "acceleration": 0.30,
            },
            "profile_b_entry_threshold": 65,
            "auto_tune_low_win_rate": 0.4,
            "auto_tune_high_win_rate": 0.7,
            "auto_tune_step": 0.05,
        }
    )


def test_momentum_and_safety_helpers() -> None:
    """Helpers produce non-negative, bounded numbers."""
    e = _engine()
    assert e.momentum_score(0.05, 6.0) > 0
    assert e.safety_score(200000, 6.0) > 30
    assert e.acceleration_score(100, 600, 0.05, 5.0) <= 150


def test_profile_a_blends_all_sub_scores() -> None:
    """Profile A uses non-zero weights on every axis."""
    e = _engine()
    inputs = ScoreInputs(social=80, wallet=50, momentum=60, safety=60, acceleration=120)
    result = e.score(inputs, have_llm_data=True)
    assert result.profile == "A"
    assert result.threshold == 200
    # final = (80*0.25 + 50*0.25 + 60*0.15 + 60*0.2 + 120*0.15) * 10
    assert result.final == pytest.approx((80 * 0.25 + 50 * 0.25 + 60 * 0.15 + 60 * 0.2 + 120 * 0.15) * 10)
    assert result.passed is (result.final >= 200)


def test_profile_b_ignores_social_and_wallet() -> None:
    """Profile B zeroes out social and wallet weights."""
    e = _engine()
    inputs = ScoreInputs(social=100, wallet=100, momentum=50, safety=60, acceleration=100)
    result = e.score(inputs, have_llm_data=False)
    assert result.profile == "B"
    assert result.threshold == 65
    expected = 0.0 * 100 + 0.0 * 100 + 0.35 * 50 + 0.35 * 60 + 0.30 * 100
    assert result.final == pytest.approx(expected)


def test_tune_profile_a_lowers_weak_source() -> None:
    """Auto-tune lowers sources with win rate below the floor."""
    e = _engine()
    tuned = e.tune_profile_a({"social": 0.30})
    assert pytest.approx(sum(tuned.values()), rel=1e-6) == 1.0
    assert tuned["social"] < 0.25
