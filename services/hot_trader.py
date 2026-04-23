"""HOT_TRADER bucket: fast scalping on trending Solana meme coins.

Polls DexScreener top-boosts/latest-boosts every N minutes, filters on
volume/liquidity/momentum thresholds, and scalps with tight stops.
No LLM validation — speed matters here.
"""

from __future__ import annotations

from typing import Any

from clients.dexscreener import DexScreenerClient
from core.time_utils import TimeProvider
from services.base_bucket import BaseBucket, BucketDeps


class HotTraderService(BaseBucket):
    """Momentum scalper."""

    name: str = "HOT_TRADER"

    def __init__(
        self,
        deps: BucketDeps,
        bucket_cfg: dict[str, Any],
        dex: DexScreenerClient,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Create the service.

        Args:
            deps: Shared dependency bundle.
            bucket_cfg: Bucket config block.
            dex: DexScreener client.
            time_provider: Optional injected clock.
        """
        super().__init__(deps, bucket_cfg)
        self._dex = dex
        self._time = time_provider or TimeProvider()

    def _passes_entry(self, pair: dict[str, Any]) -> bool:
        """Run the HOT_TRADER entry filter.

        Args:
            pair: DexScreener pair dict.

        Returns:
            True if all entry thresholds pass.
        """
        entry = self._cfg.get("entry") or {}
        liq = float((pair.get("liquidity") or {}).get("usd") or 0.0)
        vol_1h = float((pair.get("volume") or {}).get("h1") or 0.0)
        change_5m = float((pair.get("priceChange") or {}).get("m5") or 0.0) / 100.0
        change_1h = float((pair.get("priceChange") or {}).get("h1") or 0.0) / 100.0
        if liq < float(entry.get("min_liq_usd", 50000)):
            return False
        if vol_1h < float(entry.get("min_volume_1h_usd", 100000)):
            return False
        if (
            change_5m < float(entry.get("min_price_change_5m_pct", 0.02))
            and change_1h < float(entry.get("min_price_change_1h_pct", 0.08))
        ):
            return False
        return True

    async def _candidates(self) -> list[dict[str, Any]]:
        """Gather and filter DexScreener candidates.

        Returns:
            A list of pair dicts ready for entry.
        """
        pairs: list[dict[str, Any]] = []
        try:
            pairs += await self._dex.search("trending")
        except Exception:  # noqa: BLE001
            pass
        # Boost endpoints return slim records; hydrate via token detail.
        try:
            for boost in (await self._dex.top_boosts())[:20]:
                addr = boost.get("tokenAddress")
                if not addr:
                    continue
                detail = await self._dex.token_detail(addr)
                if detail:
                    pairs.append(detail)
        except Exception:  # noqa: BLE001
            pass
        seen: set[str] = set()
        filtered: list[dict[str, Any]] = []
        for p in pairs:
            addr = (p.get("baseToken") or {}).get("address")
            if not addr or addr in seen:
                continue
            seen.add(addr)
            if self._passes_entry(p):
                filtered.append(p)
        return filtered

    async def scan_and_enter(self) -> int:
        """Run one scan and open new positions where thresholds pass.

        Returns:
            Number of positions opened this tick.
        """
        if not await self.enabled():
            return 0
        candidates = await self._candidates()
        opened = 0
        for p in candidates:
            base = p.get("baseToken") or {}
            addr = base.get("address")
            sym = base.get("symbol") or "?"
            if not addr:
                continue
            ok, reason = await self.can_open(addr)
            if not ok:
                self._log.debug("skip {} ({})", sym, reason)
                continue
            size_usd = await self.position_size_usd()
            if size_usd <= 0:
                continue
            price = float(p.get("priceUsd") or 0.0)
            if price <= 0:
                continue
            liquidity = float((p.get("liquidity") or {}).get("usd") or 0.0)
            req = await self._build_trade(
                addr, sym, "buy", price, size_usd, liquidity,
                stop_loss_pct=float(self._cfg.get("stop_loss_pct", -0.04)),
                take_profit_pct=float(self._cfg.get("take_profit_pct", 0.08)),
            )
            pid = await self._deps.executor.buy(req)
            self._log.info(
                "buy {} addr={} size_usd={:.2f} price={:.6f} pos_id={}",
                sym, addr, size_usd, price, pid,
            )
            opened += 1
        return opened

    async def manage_positions(self) -> int:
        """Check stops, TPs and max-hold on every open position.

        Returns:
            Number of positions closed this tick.
        """
        rows = await self._deps.db.fetchall(
            "SELECT * FROM positions WHERE bucket_name = ? AND status = 'OPEN'",
            (self.name,),
        )
        closed = 0
        for row in rows:
            addr = row["coin_address"]
            detail = None
            try:
                detail = await self._dex.token_detail(addr)
            except Exception:  # noqa: BLE001
                detail = None
            if detail is None:
                continue
            price = float(detail.get("priceUsd") or 0.0)
            if price <= 0:
                continue
            liquidity = float((detail.get("liquidity") or {}).get("usd") or 0.0)
            reason = await self.exit_check(row, price, liquidity)
            if reason is None:
                continue
            req = await self._build_trade(
                addr, row.get("coin_symbol") or "?", "sell", price,
                size_usd=float(row["size_usd"]),
                liquidity_usd=liquidity,
                position_id=row["id"],
            )
            pnl = await self._deps.executor.sell(req, reason)
            pnl_pct = pnl / max(float(row["size_usd"]), 1e-9)
            await self.on_close(row, pnl_pct)
            closed += 1
        return closed
