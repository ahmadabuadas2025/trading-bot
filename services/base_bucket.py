"""Shared base class for the four bucket services.

Provides common helpers: balance lookup, dedup+blacklist+safety gate,
position-size calculation with regime multiplier, exit management
with stop/take-profit/trailing, and P&L post-processing (blacklist
on heavy losses, per-bucket cooldown on 3 consecutive losses).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.blacklist_manager import BlacklistManager
from core.db import Database
from core.dedup_manager import DedupManager
from core.executor import Executor, TradeRequest
from core.logger import LoggerFactory
from core.regime_client import RegimeClient
from core.safety_monitor import SafetyMonitor
from core.time_utils import TimeProvider


@dataclass
class BucketDeps:
    """Dependency bundle shared by every bucket service.

    Attributes:
        db: Connected :class:`Database`.
        executor: Concrete :class:`Executor` (paper or live).
        dedup: Cross-bucket dedup manager.
        blacklist: Blacklist manager.
        safety: Safety monitor.
        regime: Regime client.
        logger: Logger factory.
        time: Time provider.
    """

    db: Database
    executor: Executor
    dedup: DedupManager
    blacklist: BlacklistManager
    safety: SafetyMonitor
    regime: RegimeClient
    logger: LoggerFactory
    time: TimeProvider


class BaseBucket:
    """Shared behaviours for all bucket strategies."""

    name: str = "BASE"

    def __init__(self, deps: BucketDeps, bucket_cfg: dict[str, Any]) -> None:
        """Create a bucket.

        Args:
            deps: Shared :class:`BucketDeps`.
            bucket_cfg: Bucket-specific config block.
        """
        self._deps = deps
        self._cfg = bucket_cfg
        self._log = deps.logger.get(self.name)

    async def enabled(self) -> bool:
        """Whether the bucket is enabled in the DB.

        Returns:
            True if the seed row says ``enabled = 1``.
        """
        row = await self._deps.db.fetchone(
            "SELECT enabled FROM fund_buckets WHERE bucket_name = ?",
            (self.name,),
        )
        return bool(row and row.get("enabled"))

    async def balance(self) -> float:
        """Return the current bucket balance in USD.

        Returns:
            Balance in USD.
        """
        row = await self._deps.db.fetchone(
            "SELECT balance FROM fund_buckets WHERE bucket_name = ?",
            (self.name,),
        )
        return float((row or {}).get("balance") or 0.0)

    async def open_position_count(self) -> int:
        """Return how many positions the bucket currently has open.

        Returns:
            Open-position count.
        """
        row = await self._deps.db.fetchone(
            "SELECT COUNT(*) AS n FROM positions "
            "WHERE bucket_name = ? AND status = 'OPEN'",
            (self.name,),
        )
        return int((row or {}).get("n") or 0)

    async def position_size_usd(self) -> float:
        """Compute a regime-adjusted position size for the next entry.

        Returns:
            USD notional to use; ``0.0`` means skip the trade.
        """
        pct = float(self._cfg.get("position_size_pct", 0.03))
        bal = await self.balance()
        mult = self._deps.regime.get_multiplier(self.name)
        return round(bal * pct * mult, 6)

    async def can_open(self, coin_address: str) -> tuple[bool, str | None]:
        """Run the pre-open safety gate.

        Args:
            coin_address: Solana mint address.

        Returns:
            ``(True, None)`` when clear to trade, else ``(False, reason)``.
        """
        clear, reason = await self._deps.safety.is_clear_to_trade(self.name)
        if not clear:
            return (False, f"safety:{reason}")
        if await self._deps.blacklist.is_blacklisted(coin_address):
            return (False, "blacklist")
        holder = await self._deps.dedup.already_held(coin_address)
        if holder is not None:
            return (False, f"dedup:{holder}")
        max_open = int(self._cfg.get("max_open_positions", 3))
        if await self.open_position_count() >= max_open:
            return (False, "max_open_positions")
        return (True, None)

    async def exit_check(
        self,
        position: dict[str, Any],
        current_price: float,
        liquidity_usd: float,
    ) -> str | None:
        """Return an exit reason if stop/TP/trail/hold conditions trigger.

        Args:
            position: Row from ``positions``.
            current_price: Latest observed price.
            liquidity_usd: Pool liquidity at check time.

        Returns:
            Close reason string, else ``None``.
        """
        entry = float(position["entry_price"])
        if entry <= 0:
            return None
        pnl_pct = (current_price - entry) / entry
        stop = position.get("stop_loss_pct")
        tp = position.get("take_profit_pct")
        if stop is not None and pnl_pct <= float(stop):
            return "stop_loss"
        if tp is not None and pnl_pct >= float(tp):
            return "take_profit"
        arm = self._cfg.get("trailing_arm_pct")
        gap = self._cfg.get("trailing_gap_pct")
        if arm is not None and gap is not None:
            peak = float(position.get("peak_price") or entry)
            if current_price > peak:
                await self._deps.db.execute(
                    "UPDATE positions SET peak_price = ?, trailing_armed = "
                    "CASE WHEN ? >= ? THEN 1 ELSE trailing_armed END WHERE id = ?",
                    (current_price, (current_price - entry) / entry, float(arm), position["id"]),
                )
                peak = current_price
            if position.get("trailing_armed") and current_price <= peak * (1.0 - float(gap)):
                return "trailing_stop"
        return None

    async def on_close(self, position: dict[str, Any], pnl_pct: float) -> None:
        """Post-close bookkeeping: blacklist + cooldown + logs.

        Args:
            position: Row from ``positions`` before it closed.
            pnl_pct: Realised P&L fraction.
        """
        opened = position.get("opened_at")
        held_minutes = 0.0
        if opened:
            try:
                opened_dt = self._deps.time.now()  # acceptable approximation
            except Exception:  # noqa: BLE001
                opened_dt = self._deps.time.now()
            held_minutes = max(
                (self._deps.time.now() - opened_dt).total_seconds() / 60.0, 0.0
            )
        hours, permanent = self._deps.blacklist.hours_for_loss_pct(pnl_pct, held_minutes)
        if hours is not None or permanent:
            await self._deps.blacklist.add(
                position["coin_address"],
                reason="heavy_loss" if not permanent else "rug",
                source=self.name,
                coin_symbol=position.get("coin_symbol"),
                hours=hours,
                permanent=permanent,
            )
        if await self._deps.safety.consecutive_losses(self.name, n=3):
            await self._deps.safety.add_cooldown(
                self.name, hours=2.0, reason="consecutive_losses"
            )

    async def _build_trade(
        self,
        coin_address: str,
        coin_symbol: str,
        side: str,
        market_price: float,
        size_usd: float,
        liquidity_usd: float,
        *,
        position_id: int | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        atr: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TradeRequest:
        """Build a :class:`TradeRequest` with this bucket's defaults.

        Args:
            coin_address: Solana mint address.
            coin_symbol: Ticker.
            side: ``buy`` or ``sell``.
            market_price: Current mid price.
            size_usd: USD notional.
            liquidity_usd: Pool liquidity in USD.
            position_id: Optional owning-position id (for sells).
            stop_loss_pct: Optional stop-loss fraction.
            take_profit_pct: Optional take-profit fraction.
            atr: Optional ATR value.
            extra: Free-form metadata.

        Returns:
            A populated :class:`TradeRequest`.
        """
        return TradeRequest(
            bucket=self.name,
            coin_address=coin_address,
            coin_symbol=coin_symbol,
            side=side,
            market_price=market_price,
            size_usd=size_usd,
            liquidity_usd=liquidity_usd,
            position_id=position_id,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            atr=atr,
            extra=extra,
        )
