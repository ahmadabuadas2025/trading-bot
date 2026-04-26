"""Persistent coin blacklist.

Rules and durations come from the top-level spec. All reads and writes
go through SQLite so the dashboard and multiple services agree on
state.
"""

from __future__ import annotations

from typing import Any

from core.db import Database
from core.time_utils import TimeProvider


class BlacklistManager:
    """CRUD + query layer over the ``blacklist`` table."""

    PERMANENT_SECONDS: int = 100 * 365 * 24 * 3600  # ~100 years; stored as explicit date

    def __init__(self, db: Database, time_provider: TimeProvider | None = None) -> None:
        """Create a blacklist manager.

        Args:
            db: An already-connected :class:`Database`.
            time_provider: Optional injected clock for testing.
        """
        self._db = db
        self._time = time_provider or TimeProvider()

    async def add(
        self,
        coin_address: str,
        reason: str,
        source: str,
        *,
        coin_symbol: str | None = None,
        hours: float | None = None,
        permanent: bool = False,
    ) -> None:
        """Blacklist a coin.

        Args:
            coin_address: Solana mint address.
            reason: Short human-readable reason.
            source: Originating subsystem (for attribution).
            coin_symbol: Optional ticker for the dashboard.
            hours: Expiry window in hours, ignored if ``permanent``.
            permanent: If true, the entry never expires.
        """
        expires_at: str | None
        if permanent or hours is None:
            expires_at = None if permanent else self._time.add(24 * 3600).isoformat()
        else:
            expires_at = self._time.add(hours * 3600).isoformat()
        await self._db.execute(
            "INSERT INTO blacklist "
            "(coin_address, coin_symbol, reason, source, expires_at, permanent) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (coin_address, coin_symbol, reason, source, expires_at, 1 if permanent else 0),
        )

    async def is_blacklisted(self, coin_address: str) -> bool:
        """Return whether a coin is currently blacklisted.

        Args:
            coin_address: Solana mint address.

        Returns:
            True if at least one non-expired entry exists.
        """
        now_iso = self._time.now().isoformat()
        row = await self._db.fetchone(
            "SELECT 1 FROM blacklist WHERE coin_address = ? "
            "AND (permanent = 1 OR expires_at IS NULL OR expires_at > ?) LIMIT 1",
            (coin_address, now_iso),
        )
        return row is not None

    async def list_active(self) -> list[dict[str, Any]]:
        """Return all currently active blacklist entries.

        Returns:
            List of row dicts (possibly empty).
        """
        now_iso = self._time.now().isoformat()
        return await self._db.fetchall(
            "SELECT * FROM blacklist "
            "WHERE permanent = 1 OR expires_at IS NULL OR expires_at > ? "
            "ORDER BY blacklisted_at DESC",
            (now_iso,),
        )

    @staticmethod
    def hours_for_loss_pct(loss_pct: float, held_minutes: float) -> tuple[float | None, bool]:
        """Map realised-loss severity to a blacklist duration.

        Args:
            loss_pct: Trade P&L as a fraction (negative for losses).
            held_minutes: Hold duration in minutes.

        Returns:
            Tuple of (hours, permanent). ``hours=None`` + ``permanent=False``
            means no blacklist action is needed.
        """
        if loss_pct <= -0.40 and held_minutes <= 30:
            return (None, True)
        if loss_pct <= -0.30:
            return (48.0, False)
        return (None, False)

    async def purge_expired(self) -> int:
        """Delete expired, non-permanent rows.

        Returns:
            Number of rows considered for deletion. Kept for history;
            callers should usually prefer :meth:`is_blacklisted`.
        """
        now_iso = self._time.now().isoformat()
        rows = await self._db.fetchall(
            "SELECT id FROM blacklist WHERE permanent = 0 AND expires_at IS NOT NULL "
            "AND expires_at <= ?",
            (now_iso,),
        )
        return len(rows)

    @staticmethod
    def default_hours_for_rule(rule: str) -> tuple[float | None, bool]:
        """Return (hours, permanent) for a named rule.

        Args:
            rule: Rule key (e.g. ``llm_skip``, ``honeypot``).

        Returns:
            Tuple interpretable by :meth:`add`.
        """
        table: dict[str, tuple[float | None, bool]] = {
            "llm_skip": (24.0, False),
            "honeypot": (None, True),
            "heavy_loss": (48.0, False),
            "rug": (None, True),
            "top_holder": (24.0, False),
            "manual": (None, True),
        }
        return table.get(rule, (24.0, False))
