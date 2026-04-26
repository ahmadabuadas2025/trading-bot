"""Focused tests for :class:`ATRCalculator` math and fallbacks."""

from __future__ import annotations

from core.atr_calculator import ATRCalculator, _Candle


def test_atr_math_single_series() -> None:
    """Classic TR computation over a deterministic candle set."""
    candles = [
        _Candle(high=10, low=9, close=9.5),
        _Candle(high=11, low=9.5, close=10.5),
        _Candle(high=11.5, low=10, close=10.2),
    ]
    atr = ATRCalculator._atr_from_candles(candles)
    # TR_1 = max(1.5, |11-9.5|, |9.5-9.5|) = 1.5
    # TR_2 = max(1.5, |11.5-10.5|, |10-10.5|) = 1.5
    assert atr == 1.5


def test_atr_empty_list_returns_zero() -> None:
    """Fewer than two candles produces zero."""
    assert ATRCalculator._atr_from_candles([]) == 0.0
    assert ATRCalculator._atr_from_candles([_Candle(1, 1, 1)]) == 0.0
