"""Daily loss emergency stop and per-bucket cooldowns.

The monitor runs on its own interval from the orchestrator. Buckets
must also consult :meth:`SafetyMonitor.is_clear_to_trade` before every
entry because cooldowns can be added between monitor ticks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.db import Database
from core.time_utils import TimeProvider


class SafetyMonitor:
    """Enforce daily-loss and per-bucket cooldown rules."""

    def __init__(
        self,
        db: Database,
        config: dict[str, Any],
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Create a safety monitor.

        Args:
            db: Connected :class:`Database`.
            config: The ``safety`` section from ``config.yaml``.
            time_provider: Optional injected clock.
        """
        self._db = db
        self._cfg = config
        self._time = time_provider or TimeProvider()

    async def emergency_stop_active(self) -> bool:
        """Return whether the global emergency stop is set.

        Returns:
            ``True`` if trading should be blocked.
        """
        row = await self._db.fetchone(
            "SELECT emergency_stop FROM safety_state WHERE id = 1"
        )
        return bool(row and row.get("emergency_stop"))

    async def set_emergency(self, reason: str, daily_loss_pct: float) -> None:
        """Activate the emergency stop.

        Args:
            reason: Human-readable reason.
            daily_loss_pct: Daily loss fraction that triggered it.
        """
        await self._db.execute(
            "UPDATE safety_state SET emergency_stop = 1, "
            "daily_loss_pct = ?, stop_reason = ?, triggered_at = ?, reset_at = ? "
            "WHERE id = 1",
            (daily_loss_pct, reason, self._time.now_iso(), None),
        )

    async def reset_daily(self) -> None:
        """Clear the emergency stop and zero the daily loss counter."""
        await self._db.execute(
            "UPDATE safety_state SET emergency_stop = 0, daily_loss_pct = 0, "
            "stop_reason = NULL, reset_at = ? WHERE id = 1",
            (self._time.now_iso(),),
        )

    async def compute_daily_loss_pct(self) -> float:
        """Return today's realised loss as a fraction of total bankroll.

        Returns:
            Negative fraction when the day is red, else ``0.0``.
        """
        start_of_day = self._time.now().replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC
        ).isoformat()
        rows = await self._db.fetchall(
            "SELECT SUM(pnl_usd) AS pnl_sum FROM positions "
            "WHERE status = 'CLOSED' AND closed_at >= ?",
            (start_of_day,),
        )
        pnl = float((rows[0] or {}).get("pnl_sum") or 0.0)
        bankroll_rows = await self._db.fetchall("SELECT SUM(balance) AS b FROM fund_buckets")
        bankroll = float((bankroll_rows[0] or {}).get("b") or 0.0)
        if bankroll <= 0:
            return 0.0
        return pnl / bankroll if pnl < 0 else 0.0

    async def tick(self) -> None:
        """Periodic check: flip the emergency stop when threshold hit."""
        loss = await self.compute_daily_loss_pct()
        limit = float(self._cfg.get("daily_loss_emergency_pct", 0.15))
        if loss <= -abs(limit):
            await self.set_emergency(
                reason=f"Daily loss {loss:.2%} >= {limit:.2%}",
                daily_loss_pct=loss,
            )

    async def add_cooldown(self, bucket: str, hours: float, reason: str) -> None:
        """Pause a bucket for ``hours`` hours.

        Args:
            bucket: Bucket key.
            hours: Cooldown duration in hours.
            reason: Short description.
        """
        until = self._time.add(hours * 3600).isoformat()
        await self._db.execute(
            "INSERT INTO bucket_cooldowns (bucket_name, reason, cooldown_until) VALUES (?, ?, ?)",
            (bucket, reason, until),
        )

    async def is_clear_to_trade(self, bucket: str) -> tuple[bool, str | None]:
        """Whether a bucket may open new positions right now.

        Args:
            bucket: Bucket key.

        Returns:
            ``(True, None)`` when clear, else ``(False, reason)``.
        """
        if await self.emergency_stop_active():
            return (False, "emergency_stop")
        now_iso = self._time.now_iso()
        row = await self._db.fetchone(
            "SELECT reason FROM bucket_cooldowns "
            "WHERE bucket_name = ? AND cooldown_until > ? "
            "ORDER BY triggered_at DESC LIMIT 1",
            (bucket, now_iso),
        )
        if row:
            return (False, row.get("reason") or "cooldown")
        return (True, None)

    async def consecutive_losses(self, bucket: str, n: int = 3) -> bool:
        """Whether the last ``n`` closed trades for a bucket were losses.

        Args:
            bucket: Bucket key.
            n: Window size.

        Returns:
            True if all of the last ``n`` closed trades lost money.
        """
        rows = await self._db.fetchall(
            "SELECT pnl_usd FROM positions WHERE bucket_name = ? AND status = 'CLOSED' "
            "ORDER BY closed_at DESC LIMIT ?",
            (bucket, n),
        )
        if len(rows) < n:
            return False
        return all((r.get("pnl_usd") or 0) < 0 for r in rows)

    @staticmethod
    def is_today_utc(iso_ts: str | datetime | None) -> bool:
        """Helper: whether an ISO timestamp falls on today's UTC date.

        Args:
            iso_ts: Datetime or ISO-8601 string.

        Returns:
            True if same UTC calendar day as ``now``.
        """
        if iso_ts is None:
            return False
        dt = (
            iso_ts
            if isinstance(iso_ts, datetime)
            else datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        )
        now = datetime.now(tz=UTC)
        return dt.astimezone(UTC).date() == now.date()
