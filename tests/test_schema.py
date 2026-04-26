"""Smoke tests for schema initialisation."""

from __future__ import annotations

import pytest

from core.db import Database
from core.schema import SchemaManager


@pytest.mark.asyncio()
async def test_initial_seed_buckets(tmp_path) -> None:
    """Buckets are seeded with the correct allocations.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    db = Database(tmp_path / "seed.db")
    await db.connect()
    try:
        await SchemaManager(db).initialize(starting_balance_usd=1000.0)
        rows = await db.fetchall("SELECT bucket_name, balance FROM fund_buckets ORDER BY bucket_name")
        names = [r["bucket_name"] for r in rows]
        assert names == ["ARBITRAGE", "COPY_TRADER", "GEM_HUNTER", "HOT_TRADER"]
        balances = {r["bucket_name"]: r["balance"] for r in rows}
        assert balances["GEM_HUNTER"] == pytest.approx(400.0)
        assert balances["HOT_TRADER"] == pytest.approx(100.0)
    finally:
        await db.close()
