"""Tests for :mod:`core.regime_client` classification + multipliers."""

from __future__ import annotations

from core.regime_client import RegimeClient


def _client() -> RegimeClient:
    """Create a regime client with no I/O dependencies.

    Returns:
        A :class:`RegimeClient` suitable for unit tests.
    """
    cfg = {
        "bullish_btc_threshold": -0.02,
        "bullish_sol_threshold": -0.03,
        "bullish_fg_threshold": 50,
        "bearish_btc_threshold": -0.05,
        "bearish_fg_threshold": 35,
        "multipliers": {
            "BULLISH": {"HOT_TRADER": 1.0, "GEM_HUNTER": 1.0, "NEW_LISTING": 1.0, "COPY_TRADER": 1.0},
            "NEUTRAL": {"HOT_TRADER": 0.5, "GEM_HUNTER": 0.5, "NEW_LISTING": 0.5, "COPY_TRADER": 0.5},
            "BEARISH": {"HOT_TRADER": 0.0, "GEM_HUNTER": 0.25, "NEW_LISTING": 0.0, "COPY_TRADER": 0.25},
        },
    }
    # HttpClient and Database aren't used by _classify / get_multiplier.
    return RegimeClient(http=None, db=None, config=cfg)  # type: ignore[arg-type]


def test_classify_bullish() -> None:
    """All-green inputs produce BULLISH."""
    assert _client()._classify(btc=0.01, sol=0.02, fg=70) == "BULLISH"


def test_classify_bearish_on_btc() -> None:
    """A BTC crash alone tips the market to BEARISH."""
    assert _client()._classify(btc=-0.08, sol=0.05, fg=60) == "BEARISH"


def test_classify_bearish_on_fear() -> None:
    """Extreme fear tips the market to BEARISH."""
    assert _client()._classify(btc=0.0, sol=0.0, fg=20) == "BEARISH"


def test_classify_neutral_fallback() -> None:
    """Mixed signals fall back to NEUTRAL."""
    assert _client()._classify(btc=-0.01, sol=-0.01, fg=45) == "NEUTRAL"


def test_get_multiplier_defaults_to_neutral() -> None:
    """Before the first refresh, current() returns NEUTRAL."""
    c = _client()
    assert c.get_multiplier("HOT_TRADER") == 0.5
    assert c.get_multiplier("GEM_HUNTER") == 0.5
