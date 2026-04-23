"""Cross-bucket position deduplication.

Before any bucket opens a position the orchestrator asks the
DedupManager whether another bucket already holds the same coin.
Priority: GEM_HUNTER > NEW_LISTING > COPY_TRADER > HOT_TRADER.
"""

from __future__ import annotations

from core.db import Database


class DedupManager:
    """Check whether a coin is already held in any bucket."""

    BUCKET_PRIORITY: tuple[str, ...] = (
        "GEM_HUNTER",
        "NEW_LISTING",
        "COPY_TRADER",
        "HOT_TRADER",
    )

    def __init__(self, db: Database) -> None:
        """Create a dedup manager.

        Args:
            db: An already-connected :class:`Database`.
        """
        self._db = db

    async def already_held(self, coin_address: str) -> str | None:
        """Return the bucket holding the coin, or ``None``.

        Args:
            coin_address: Solana mint address.

        Returns:
            The holding bucket name, else ``None``.
        """
        row = await self._db.fetchone(
            "SELECT bucket_name FROM positions "
            "WHERE coin_address = ? AND status = 'OPEN' LIMIT 1",
            (coin_address,),
        )
        return None if row is None else row["bucket_name"]

    @classmethod
    def highest_priority(cls, candidates: list[str]) -> str | None:
        """Pick the highest-priority bucket from a shortlist.

        Args:
            candidates: Bucket names that want the same coin right now.

        Returns:
            The highest-priority bucket, else ``None`` if empty.
        """
        if not candidates:
            return None
        ranked = {b: i for i, b in enumerate(cls.BUCKET_PRIORITY)}
        return min(candidates, key=lambda b: ranked.get(b, len(ranked)))
