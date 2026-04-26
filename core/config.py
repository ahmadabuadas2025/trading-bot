"""Config loader for SolanaTradingBot.

Loads :file:`config.yaml` for all non-secret parameters and the
process environment / ``.env`` file for secrets. Provides strongly
typed accessors so services do not read raw dicts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class Secrets:
    """Container for environment-provided secrets.

    Attributes:
        wallet_private_key: Base58 Solana private key (live mode only).
        openrouter_api_key: OpenRouter API key for LLM calls.
        lunarcrush_api_key: Optional LunarCrush free-tier key.
        helius_api_key: Optional Helius RPC/websocket key.
        birdeye_api_key: Optional Birdeye API key.
        solana_rpc_url: Explicit RPC override, else derived.
    """

    wallet_private_key: str | None = None
    openrouter_api_key: str | None = None
    lunarcrush_api_key: str | None = None
    helius_api_key: str | None = None
    birdeye_api_key: str | None = None
    solana_rpc_url: str | None = None


@dataclass
class AppConfig:
    """Top-level configuration object.

    Attributes:
        raw: The full parsed YAML tree, for features that want to
            consume deeply nested sections verbatim.
        secrets: Resolved secrets from the environment.
        mode: The effective trading mode (``paper`` or ``live``).
        root: Absolute path to the project root.
    """

    raw: dict[str, Any]
    secrets: Secrets
    mode: str
    root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)

    def section(self, name: str) -> dict[str, Any]:
        """Return a top-level config section or an empty dict.

        Args:
            name: Section key in ``config.yaml``.

        Returns:
            The section dict, or an empty dict if absent.
        """
        value = self.raw.get(name, {})
        if not isinstance(value, dict):
            raise TypeError(f"Config section {name!r} is not a mapping")
        return value

    def bucket(self, bucket_name: str) -> dict[str, Any]:
        """Return the config block for a specific bucket.

        Args:
            bucket_name: One of ``HOT_TRADER``, ``COPY_TRADER``,
                ``GEM_HUNTER`` or ``ARBITRAGE``.

        Returns:
            The bucket configuration mapping.
        """
        buckets = self.section("buckets")
        if bucket_name not in buckets:
            raise KeyError(f"Unknown bucket {bucket_name!r}")
        return buckets[bucket_name]


class ConfigLoader:
    """Load ``config.yaml`` and ``.env`` into an :class:`AppConfig`."""

    def __init__(self, config_path: Path | str = "config.yaml") -> None:
        """Create a loader.

        Args:
            config_path: Path to the YAML configuration file.
        """
        self._config_path = Path(config_path)

    def load(self, mode_override: str | None = None) -> AppConfig:
        """Parse YAML and environment and return an :class:`AppConfig`.

        Args:
            mode_override: Optional ``paper``/``live`` override from the
                CLI; takes precedence over ``mode.default`` in YAML.

        Returns:
            A fully populated :class:`AppConfig`.
        """
        load_dotenv(override=False)
        with self._config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        if not isinstance(raw, dict):
            raise TypeError("config.yaml must be a mapping at the top level")

        mode = mode_override or raw.get("mode", {}).get("default", "paper")
        if mode not in {"paper", "live"}:
            raise ValueError(f"Invalid mode {mode!r}; expected 'paper' or 'live'")

        secrets = Secrets(
            wallet_private_key=os.getenv("WALLET_PRIVATE_KEY") or None,
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            lunarcrush_api_key=os.getenv("LUNARCRUSH_API_KEY") or None,
            helius_api_key=os.getenv("HELIUS_API_KEY") or None,
            birdeye_api_key=os.getenv("BIRDEYE_API_KEY") or None,
            solana_rpc_url=os.getenv("SOLANA_RPC_URL") or None,
        )
        return AppConfig(raw=raw, secrets=secrets, mode=mode)
