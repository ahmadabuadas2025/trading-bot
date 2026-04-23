"""Async tests for dedup and blacklist managers backed by SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.blacklist_manager import BlacklistManager
from core.db import Database
from core.dedup_manager import DedupManager
from core.schema import SchemaManager
from core.time_utils import TimeProvider


class _FrozenTime(TimeProvider):
    """A :class:`TimeProvider` whose ``now`` is fixed."""

    def __init__(self, fixed: datetime) -> None:
        """Create a frozen time.

        Args:
            fixed: The time to return from every call.
        """
        self._fixed = fixed

    def now(self) -> datetime:
        """Return the frozen time.

        Returns:
            The fixed datetime.
        """
        return self._fixed


@pytest.fixture()
async def db(tmp_path) -> Database:
    """Create a fresh SQLite database for each test.

    Args:
        tmp_path: Pytest temporary directory.

    Yields:
        A connected :class:`Database` with the full schema applied.
    """
    database = Database(tmp_path / "test.db")
    await database.connect()
    await SchemaManager(database).initialize(starting_balance_usd=1000.0)
    yield database
    await database.close()


@pytest.mark.asyncio()
async def test_dedup_detects_cross_bucket_positions(db: Database) -> None:
    """An open position in any bucket is surfaced by DedupManager."""
    await db.execute(
        "INSERT INTO positions "
        "(bucket_name, coin_address, coin_symbol, entry_price, size_tokens, "
        "size_usd, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("GEM_HUNTER", "ABC", "TEST", 1.0, 100.0, 100.0, "OPEN"),
    )
    dedup = DedupManager(db)
    holder = await dedup.already_held("ABC")
    assert holder == "GEM_HUNTER"
    assert await dedup.already_held("OTHER") is None


def test_dedup_priority_order() -> None:
    """Highest priority bucket wins ties."""
    assert DedupManager.highest_priority(["HOT_TRADER", "GEM_HUNTER"]) == "GEM_HUNTER"
    assert DedupManager.highest_priority([]) is None


@pytest.mark.asyncio()
async def test_blacklist_expiry(db: Database) -> None:
    """A time-bounded blacklist entry stops being active after expiry."""
    frozen = datetime.now(tz=UTC)
    bl = BlacklistManager(db, _FrozenTime(frozen))
    await bl.add("MINT", "llm_skip", source="test", hours=1.0)
    assert await bl.is_blacklisted("MINT") is True

    future = BlacklistManager(db, _FrozenTime(frozen + timedelta(hours=2)))
    assert await future.is_blacklisted("MINT") is False


@pytest.mark.asyncio()
async def test_blacklist_permanent(db: Database) -> None:
    """Permanent entries stay forever."""
    bl = BlacklistManager(db)
    await bl.add("POT", "honeypot", source="test", permanent=True)
    assert await bl.is_blacklisted("POT") is True


def test_hours_for_loss_pct_classification() -> None:
    """Rug, heavy loss, and mild loss map to correct durations."""
    assert BlacklistManager.hours_for_loss_pct(-0.45, 10)[1] is True
    hours, permanent = BlacklistManager.hours_for_loss_pct(-0.35, 120)
    assert hours == 48.0
    assert permanent is False
    assert BlacklistManager.hours_for_loss_pct(-0.05, 30) == (None, False)
