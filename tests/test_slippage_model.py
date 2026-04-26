"""Tests for :mod:`core.slippage_model`."""

from __future__ import annotations

import pytest

from core.slippage_model import SlippageModel


@pytest.fixture()
def model() -> SlippageModel:
    """Build a :class:`SlippageModel` with default configuration.

    Returns:
        A configured slippage model.
    """
    return SlippageModel(
        {
            "base_slippage_pct": 0.005,
            "low_liq_threshold": 50000,
            "low_liq_extra_slippage": 0.03,
            "very_low_liq_threshold": 20000,
            "very_low_liq_extra_slippage": 0.05,
            "price_impact_factor": 0.10,
            "solana_base_fee_sol": 0.000005,
            "solana_priority_fee_sol": 0.0001,
            "jupiter_fee_pct": 0.002,
            "fallback_sol_usd": 100.0,
        }
    )


def test_base_slippage_when_deep_liquidity(model: SlippageModel) -> None:
    """Deep liquidity applies only the base slippage plus impact."""
    slip = model.compute_slippage(liquidity_usd=10_000_000, trade_size_usd=1_000)
    # base 0.5% + impact 1000/10M * 0.10 ~ 0.005 + 1e-5
    assert slip == pytest.approx(0.005 + (1000 / 10_000_000) * 0.10)


def test_low_liquidity_adds_extra_slippage(model: SlippageModel) -> None:
    """Liquidity below the low-liq threshold applies the extra fee."""
    slip = model.compute_slippage(liquidity_usd=30_000, trade_size_usd=500)
    assert slip > 0.005 + 0.03 - 1e-9


def test_very_low_liquidity_stacks_both_extras(model: SlippageModel) -> None:
    """Very-low liquidity stacks both low-liq additions."""
    slip = model.compute_slippage(liquidity_usd=10_000, trade_size_usd=100)
    assert slip >= 0.005 + 0.03 + 0.05


def test_buy_executes_above_market(model: SlippageModel) -> None:
    """Buys execute above the mid price."""
    res = model.simulate("buy", market_price=1.0, trade_size_usd=100, liquidity_usd=100_000)
    assert res.executed_price > res.market_price
    assert res.size_tokens > 0
    assert res.fee_usd > 0


def test_sell_executes_below_market(model: SlippageModel) -> None:
    """Sells execute below the mid price."""
    res = model.simulate("sell", market_price=2.0, trade_size_usd=200, liquidity_usd=100_000)
    assert res.executed_price < res.market_price


def test_invalid_side_raises(model: SlippageModel) -> None:
    """An unsupported side raises ``ValueError``."""
    with pytest.raises(ValueError):
        model.simulate("swap", market_price=1.0, trade_size_usd=10, liquidity_usd=50_000)
