"""ARBITRAGE bucket: cross-DEX arbitrage on Solana.

Compares prices for the same token across different DEX pools
(Raydium, Orca, Meteora, etc.) using DexScreener, and also
fetches Jupiter quotes to detect route-level price discrepancies.
When a spread exceeds the configured threshold, executes a buy on
the cheaper pool and sell on the more expensive one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from clients.dexscreener import DexScreenerClient
from clients.jupiter import JupiterClient
from core.scoring_engine import ScoringEngine
from services.base_bucket import BaseBucket, BucketDeps


class ArbitrageService(BaseBucket):
    """Cross-DEX arbitrage scanner and executor."""

    name: str = "ARBITRAGE"

    def __init__(
        self,
        deps: BucketDeps,
        bucket_cfg: dict[str, Any],
        dex: DexScreenerClient,
        jupiter: JupiterClient,
        scoring: ScoringEngine,
    ) -> None:
        """Create the service.

        Args:
            deps: Shared dependency bundle.
            bucket_cfg: Bucket config block.
            dex: DexScreener client.
            jupiter: Jupiter client for quote comparison.
            scoring: Scoring engine.
        """
        super().__init__(deps, bucket_cfg)
        self._dex = dex
        self._jupiter = jupiter
        self._scoring = scoring

    async def _gather_tokens(self) -> list[dict[str, Any]]:
        """Gather high-volume Solana tokens from DexScreener.

        Returns:
            Deduplicated list of pair dicts sorted by 1h volume descending.
        """
        queries = ["solana", "raydium", "orca", "jupiter", "meteora"]
        pairs: list[dict[str, Any]] = []
        for q in queries:
            try:
                pairs += await self._dex.search(q)
            except Exception:  # noqa: BLE001
                pass
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for p in pairs:
            addr = (p.get("baseToken") or {}).get("address")
            if not addr or addr in seen:
                continue
            seen.add(addr)
            unique.append(p)
        unique.sort(
            key=lambda p: float((p.get("volume") or {}).get("h1") or 0.0),
            reverse=True,
        )
        return unique[:40]

    async def _find_spreads(
        self, token_addr: str
    ) -> list[dict[str, Any]]:
        """Find price spreads across DEX pools for a single token.

        Args:
            token_addr: Solana mint address.

        Returns:
            List of spread opportunity dicts with pool info and spread_pct.
        """
        entry = self._cfg.get("entry") or {}
        min_spread = float(entry.get("min_spread_pct", 0.005))
        min_vol = float(entry.get("min_volume_1h_usd", 50000))
        min_liq = float(entry.get("min_liq_usd", 20000))

        try:
            detail_data = await self._dex.token_detail(token_addr)
        except Exception:  # noqa: BLE001
            return []
        if detail_data is None:
            return []

        # token_detail returns only the first Solana pair; fetch all
        # pairs by querying the search endpoint with the address.
        all_pairs: list[dict[str, Any]] = []
        try:
            search_results = await self._dex.search(token_addr)
            all_pairs = [
                p for p in search_results
                if (p.get("baseToken") or {}).get("address") == token_addr
            ]
        except Exception:  # noqa: BLE001
            pass
        if not all_pairs:
            all_pairs = [detail_data]

        # Filter pools with sufficient volume and liquidity.
        viable: list[dict[str, Any]] = []
        for p in all_pairs:
            price = float(p.get("priceUsd") or 0.0)
            vol = float((p.get("volume") or {}).get("h1") or 0.0)
            liq = float((p.get("liquidity") or {}).get("usd") or 0.0)
            if price > 0 and vol >= min_vol and liq >= min_liq:
                viable.append(p)

        if len(viable) < 2:
            return []

        # Compare all pool-pairs for spreads.
        opps: list[dict[str, Any]] = []
        for i, low in enumerate(viable):
            for high in viable[i + 1 :]:
                price_low = float(low.get("priceUsd") or 0.0)
                price_high = float(high.get("priceUsd") or 0.0)
                if price_low <= 0 or price_high <= 0:
                    continue
                if price_low > price_high:
                    price_low, price_high = price_high, price_low
                    low_pair, high_pair = high, low
                else:
                    low_pair, high_pair = low, high
                spread_pct = (price_high - price_low) / price_low
                if spread_pct >= min_spread:
                    opps.append({
                        "token_addr": token_addr,
                        "buy_pair": low_pair,
                        "sell_pair": high_pair,
                        "buy_price": price_low,
                        "sell_price": price_high,
                        "spread_pct": spread_pct,
                        "buy_liq": float(
                            (low_pair.get("liquidity") or {}).get("usd") or 0.0
                        ),
                        "sell_liq": float(
                            (high_pair.get("liquidity") or {}).get("usd") or 0.0
                        ),
                    })
        return opps

    async def _jupiter_spread(self, token_addr: str) -> float | None:
        """Check Jupiter quotes at different slippage levels for price delta.

        Args:
            token_addr: Solana mint address.

        Returns:
            Spread percentage between best and worst quote, or None.
        """
        try:
            amount = 1_000_000_000  # 1 SOL in lamports
            quote_tight = await self._jupiter.quote(
                self._jupiter.WSOL_MINT, token_addr, amount, slippage_bps=50,
            )
            quote_loose = await self._jupiter.quote(
                self._jupiter.WSOL_MINT, token_addr, amount, slippage_bps=300,
            )
            out_tight = int(quote_tight.get("outAmount") or 0)
            out_loose = int(quote_loose.get("outAmount") or 0)
            if out_tight > 0 and out_loose > 0:
                spread = abs(out_tight - out_loose) / max(out_tight, out_loose)
                return spread
        except Exception:  # noqa: BLE001
            pass
        return None

    async def scan_and_enter(self) -> int:
        """Scan for cross-DEX arbitrage opportunities and enter trades.

        Returns:
            Number of positions opened this tick.
        """
        if not await self.enabled():
            return 0
        tokens = await self._gather_tokens()
        opened = 0
        for token_pair in tokens:
            base = token_pair.get("baseToken") or {}
            addr = base.get("address")
            sym = base.get("symbol") or "?"
            if not addr:
                continue

            # Check for DEX pool spreads.
            opps = await self._find_spreads(addr)

            # Also check Jupiter route spreads.
            jup_spread = await self._jupiter_spread(addr)
            if jup_spread is not None:
                entry = self._cfg.get("entry") or {}
                min_spread = float(entry.get("min_spread_pct", 0.005))
                if jup_spread >= min_spread and not opps:
                    price = float(token_pair.get("priceUsd") or 0.0)
                    liq = float(
                        (token_pair.get("liquidity") or {}).get("usd") or 0.0
                    )
                    if price > 0:
                        opps.append({
                            "token_addr": addr,
                            "buy_pair": token_pair,
                            "sell_pair": token_pair,
                            "buy_price": price,
                            "sell_price": price * (1 + jup_spread),
                            "spread_pct": jup_spread,
                            "buy_liq": liq,
                            "sell_liq": liq,
                        })

            if not opps:
                continue

            # Take the best spread opportunity.
            best = max(opps, key=lambda o: o["spread_pct"])

            ok, reason = await self.can_open(addr)
            if not ok:
                self._log.debug("skip {} ({})", sym, reason)
                continue

            size_usd = await self.position_size_usd()
            if size_usd <= 0:
                continue

            buy_price = best["buy_price"]
            buy_liq = best["buy_liq"]
            req = await self._build_trade(
                addr,
                sym,
                "buy",
                buy_price,
                size_usd,
                buy_liq,
                stop_loss_pct=float(self._cfg.get("stop_loss_pct", -0.02)),
                take_profit_pct=float(self._cfg.get("take_profit_pct", 0.03)),
                extra={
                    "strategy": "arbitrage",
                    "spread_pct": best["spread_pct"],
                    "sell_price": best["sell_price"],
                },
            )
            pid = await self._deps.executor.buy(req)
            self._log.info(
                "arb_buy {} spread={:.4f} buy={:.6f} sell={:.6f} pos_id={}",
                sym,
                best["spread_pct"],
                best["buy_price"],
                best["sell_price"],
                pid,
            )

            # Execute the sell leg immediately to complete the arb.
            # Wrapped in try/except so a failed sell doesn't abort the
            # remaining token scan — manage_positions will clean up any
            # residual OPEN positions within max_hold_minutes.
            try:
                sell_price = best["sell_price"]
                sell_liq = best["sell_liq"]
                sell_req = await self._build_trade(
                    addr,
                    sym,
                    "sell",
                    sell_price,
                    size_usd=size_usd,
                    liquidity_usd=sell_liq,
                    position_id=pid,
                )
                pnl = await self._deps.executor.sell(sell_req, "arb_spread")
                self._log.info(
                    "arb_sell {} pnl={:.4f} pos_id={}",
                    sym, pnl, pid,
                )

                pos_row = await self._deps.db.fetchone(
                    "SELECT * FROM positions WHERE id = ?", (pid,)
                )
                if pos_row:
                    pnl_pct = pnl / max(size_usd, 1e-9)
                    await self.on_close(pos_row, pnl_pct)
            except Exception as err:  # noqa: BLE001
                self._log.warning(
                    "arb sell leg failed for {} pos_id={}: {}", sym, pid, err
                )

            opened += 1
        return opened

    def _hold_exceeded(self, position: dict[str, Any]) -> bool:
        """Check whether a position has exceeded max_hold_minutes.

        Args:
            position: Row from ``positions``.

        Returns:
            True if hold time exceeds the configured maximum.
        """
        max_hold = float(self._cfg.get("max_hold_minutes", 5))
        opened_at = position.get("opened_at")
        if not opened_at:
            return False
        try:
            if isinstance(opened_at, str):
                opened_dt = datetime.fromisoformat(opened_at).replace(
                    tzinfo=UTC
                )
            else:
                opened_dt = opened_at
            elapsed = (self._deps.time.now() - opened_dt).total_seconds() / 60.0
            return elapsed >= max_hold
        except (ValueError, TypeError):
            return False

    async def manage_positions(self) -> int:
        """Exit arb positions that exceeded max_hold_minutes.

        Most arb positions are closed immediately in scan_and_enter
        (both legs executed atomically). This method catches any
        residual open positions that were not closed — e.g. if the
        sell leg failed — and force-exits them after max_hold_minutes.

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
            try:
                detail = await self._dex.token_detail(addr)
            except Exception:  # noqa: BLE001
                detail = None
            if detail is None:
                continue
            price = float(detail.get("priceUsd") or 0.0)
            if price <= 0:
                continue
            liquidity = float(
                (detail.get("liquidity") or {}).get("usd") or 0.0
            )
            reason = await self.exit_check(row, price, liquidity)
            if reason is None and self._hold_exceeded(row):
                reason = "max_hold_exceeded"
            if reason is None:
                continue
            req = await self._build_trade(
                addr,
                row.get("coin_symbol") or "?",
                "sell",
                price,
                size_usd=float(row["size_usd"]),
                liquidity_usd=liquidity,
                position_id=row["id"],
            )
            pnl = await self._deps.executor.sell(req, reason)
            pnl_pct = pnl / max(float(row["size_usd"]), 1e-9)
            await self.on_close(row, pnl_pct)
            closed += 1
        return closed
