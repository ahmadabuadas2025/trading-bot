"""Tests for anti-rug and token validation logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import SafetyConfig
from core.models import TokenInfo
from safety.anti_rug import AntiRugEngine
from safety.honeypot_detector import HoneypotDetector
from safety.token_validator import TokenValidator


@pytest.fixture
def safety_config() -> SafetyConfig:
    return SafetyConfig(
        max_risk_score=50,
        reject_mint_authority=True,
        reject_freeze_authority=True,
        max_top_holder_pct=0.25,
        min_token_age_seconds=30,
        restricted_age_seconds=300,
        honeypot_check_enabled=True,
    )


@pytest.fixture
def mock_solana_data() -> MagicMock:
    data = MagicMock()
    data.get_token_info = AsyncMock(
        return_value=TokenInfo(address="test_token", supply=1_000_000, decimals=9)
    )
    data.get_token_holders = AsyncMock(return_value=500)
    data.get_token_age_seconds = AsyncMock(return_value=3600.0)
    return data


@pytest.fixture
def mock_jupiter_client() -> MagicMock:
    client = MagicMock()
    client.get_quote = AsyncMock(
        return_value={"outAmount": "1000000000"}
    )
    return client


@pytest.fixture
def token_validator(safety_config: SafetyConfig, mock_solana_data: MagicMock) -> TokenValidator:
    return TokenValidator(safety_config, mock_solana_data)


@pytest.fixture
def honeypot_detector(
    safety_config: SafetyConfig, mock_jupiter_client: MagicMock
) -> HoneypotDetector:
    return HoneypotDetector(safety_config, mock_jupiter_client)


@pytest.fixture
def anti_rug(
    token_validator: TokenValidator, honeypot_detector: HoneypotDetector
) -> AntiRugEngine:
    return AntiRugEngine(token_validator, honeypot_detector)


class TestTokenValidator:
    """Test individual token validation checks."""

    @pytest.mark.asyncio
    async def test_valid_token_passes(self, token_validator: TokenValidator) -> None:
        is_valid, details = await token_validator.validate("test_token")
        assert is_valid is True
        assert details["mint_authority"] is True
        assert details["token_age"] is True

    @pytest.mark.asyncio
    async def test_young_token_rejected(
        self, token_validator: TokenValidator, mock_solana_data: MagicMock
    ) -> None:
        mock_solana_data.get_token_age_seconds = AsyncMock(return_value=10.0)

        is_valid, details = await token_validator.validate("young_token")
        assert is_valid is False
        assert details["token_age"] is False

    @pytest.mark.asyncio
    async def test_low_holders_rejected(
        self, token_validator: TokenValidator, mock_solana_data: MagicMock
    ) -> None:
        mock_solana_data.get_token_holders = AsyncMock(return_value=5)

        is_valid, details = await token_validator.validate("low_holder_token")
        assert is_valid is False
        assert details["holder_concentration"] is False

    @pytest.mark.asyncio
    async def test_no_supply_rejected(
        self, token_validator: TokenValidator, mock_solana_data: MagicMock
    ) -> None:
        mock_solana_data.get_token_info = AsyncMock(
            return_value=TokenInfo(address="bad_token", supply=0, decimals=9)
        )

        is_valid, details = await token_validator.validate("bad_token")
        assert is_valid is False


class TestHoneypotDetector:
    """Test honeypot detection logic."""

    @pytest.mark.asyncio
    async def test_safe_token_not_honeypot(self, honeypot_detector: HoneypotDetector) -> None:
        # Buy returns 1B, sell of 1B returns ~100M lamports (normal slippage)
        honeypot_detector._jupiter.get_quote = AsyncMock(
            side_effect=[
                {"outAmount": "1000000000"},
                {"outAmount": "90000000"},  # 90% return = 10% loss, OK
            ]
        )
        result = await honeypot_detector.is_honeypot("safe_token")
        assert result is False

    @pytest.mark.asyncio
    async def test_honeypot_detected_zero_sell(
        self, honeypot_detector: HoneypotDetector
    ) -> None:
        honeypot_detector._jupiter.get_quote = AsyncMock(
            side_effect=[
                {"outAmount": "1000000000"},
                {"outAmount": "0"},
            ]
        )
        result = await honeypot_detector.is_honeypot("honeypot_token")
        assert result is True

    @pytest.mark.asyncio
    async def test_honeypot_detected_high_loss(
        self, honeypot_detector: HoneypotDetector
    ) -> None:
        honeypot_detector._jupiter.get_quote = AsyncMock(
            side_effect=[
                {"outAmount": "1000000000"},
                {"outAmount": "10000000"},  # 90% round-trip loss
            ]
        )
        result = await honeypot_detector.is_honeypot("bad_token")
        assert result is True

    @pytest.mark.asyncio
    async def test_honeypot_check_disabled(self, mock_jupiter_client: MagicMock) -> None:
        config = SafetyConfig(honeypot_check_enabled=False)
        detector = HoneypotDetector(config, mock_jupiter_client)
        result = await detector.is_honeypot("any_token")
        assert result is False


class TestAntiRugEngine:
    """Test the orchestrated safety check pipeline."""

    @pytest.mark.asyncio
    async def test_safe_token_passes(self, anti_rug: AntiRugEngine) -> None:
        # Mock honeypot detector to return non-honeypot
        anti_rug._honeypot_detector._jupiter.get_quote = AsyncMock(
            side_effect=[
                {"outAmount": "1000000000"},
                {"outAmount": "90000000"},
            ]
        )
        is_safe, details = await anti_rug.validate_token("safe_token")
        assert is_safe is True
        assert details["overall_safe"] is True

    @pytest.mark.asyncio
    async def test_honeypot_fails_validation(self, anti_rug: AntiRugEngine) -> None:
        anti_rug._honeypot_detector._jupiter.get_quote = AsyncMock(
            side_effect=[
                {"outAmount": "1000000000"},
                {"outAmount": "0"},
            ]
        )
        is_safe, details = await anti_rug.validate_token("honeypot")
        assert is_safe is False
        assert details["honeypot"] is True
