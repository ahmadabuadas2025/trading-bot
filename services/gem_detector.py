"""GEM_HUNTER bucket: hidden low-cap gems.

Scans DexScreener for small, active coins, blends math + LLM scores
(Profile A when an LLM verdict exists, Profile B otherwise) and uses
ATR-based exits.
"""

from __future__ import annotations

from typing import Any

from clients.dexscreener import DexScreenerClient
from core.atr_calculator import ATRCalculator
from core.llm_scanner import LLMScanner
from core.scoring_engine import ScoreInputs, ScoringEngine
from services.base_bucket import BaseBucket, BucketDeps
from utils.honeypot import HoneypotChecker


class GemDetectorService(BaseBucket):
    """Hidden-gem hunter with LLM-aware scoring."""

    name: str = "GEM_HUNTER"

    def __init__(
        self,
        deps: BucketDeps,
        bucket_cfg: dict[str, Any],
        dex: DexScreenerClient,
        scoring: ScoringEngine,
        llm_scanner: LLMScanner,
        atr: ATRCalculator,
        honeypot: HoneypotChecker,
    ) -> None:
        """Create the service.

        Args:
            deps: Shared dependency bundle.
            bucket_cfg: Bucket config block.
            dex: DexScreener client.
            scoring: Scoring engine.
            llm_scanner: LLM scanner for verdict lookup.
            atr: ATR calculator.
            honeypot: Honeypot checker.
        """
        super().__init__(deps, bucket_cfg)
        self._dex = dex
        self._scoring = scoring
        self._llm = llm_scanner
        self._atr = atr
        self._honeypot = honeypot

    def _passes_prefilter(self, pair: dict[str, Any]) -> bool:
        """First-line entry filter based on liquidity, age, momentum.

        Args:
            pair: DexScreener pair dict.

        Returns:
            True if it survives the pre-filter.
        """
        entry = self._cfg.get("entry") or {}
        liq = float((pair.get("liquidity") or {}).get("usd") or 0.0)
        vol_1h = float((pair.get("volume") or {}).get("h1") or 0.0)
        change_1h = float((pair.get("priceChange") or {}).get("h1") or 0.0) / 100.0
        age_ms = int(pair.get("pairCreatedAt") or 0)
        if liq < float(entry.get("min_liq_usd", 10000)):
            return False
        if liq > float(entry.get("max_liq_usd", 500000)):
            return False
        ratio = vol_1h / max(liq, 1.0)
        if ratio < float(entry.get("min_vol_liq_ratio", 3.0)):
            return False
        if change_1h <= 0:
            return False
        if age_ms:
            age_hours = (self._deps.time.now().timestamp() - age_ms / 1000.0) / 3600.0
            if age_hours > float(entry.get("max_age_hours", 24)):
                return False
        return True

    async def _gather(self) -> list[dict[str, Any]]:
        """Collect candidates from several DexScreener searches.

        Returns:
            Deduplicated list of pair dicts.
        """
        queries = ["pump", "raydium", "orca", "meteora", "jupiter"]
        seen: dict[str, dict[str, Any]] = {}
        for q in queries:
            try:
                for p in await self._dex.search(q):
                    addr = (p.get("baseToken") or {}).get("address")
                    if addr and addr not in seen:
                        seen[addr] = p
            except Exception:  # noqa: BLE001
                continue
        return [p for p in seen.values() if self._passes_prefilter(p)]

    async def _score(
        self, pair: dict[str, Any]
    ) -> tuple[ScoreInputs, dict[str, Any] | None]:
        """Compute :class:`ScoreInputs` and fetch any LLM verdict.

        Args:
            pair: DexScreener pair dict.

        Returns:
            Tuple of (sub-scores, LLM verdict or ``None``).
        """
        liq = float((pair.get("liquidity") or {}).get("usd") or 0.0)
        vol_1h = float((pair.get("volume") or {}).get("h1") or 0.0)
        vol_5m = float((pair.get("volume") or {}).get("m5") or 0.0)
        change_1h = float((pair.get("priceChange") or {}).get("h1") or 0.0) / 100.0
        ratio = vol_1h / max(liq, 1.0)
        inputs = ScoreInputs(
            momentum=self._scoring.momentum_score(change_1h, ratio),
            safety=self._scoring.safety_score(liq, ratio),
            acceleration=self._scoring.acceleration_score(vol_5m, vol_1h, change_1h, ratio),
        )
        addr = (pair.get("baseToken") or {}).get("address") or ""
        verdict = await self._llm.latest_verdict(addr) if addr else None
        if verdict is not None:
            inputs.social = float(verdict.get("llm_score") or 0) * 10.0
            inputs.wallet = 100.0 if verdict.get("kol_mentioned") else 30.0
        return inputs, verdict

    async def scan_and_enter(self) -> int:
        """Score, check honeypot, and open positions that pass.

        Returns:
            Number of positions opened this tick.
        """
        if not await self.enabled():
            return 0
        opened = 0
        for pair in await self._gather():
            base = pair.get("baseToken") or {}
            addr = base.get("address")
            sym = base.get("symbol") or "?"
            if not addr:
                continue
            ok, reason = await self.can_open(addr)
            if not ok:
                self._log.debug("skip {} ({})", sym, reason)
                continue
            inputs, verdict = await self._score(pair)
            if verdict is not None and (verdict.get("verdict") or "").upper() == "SKIP":
                await self._deps.blacklist.add(
                    addr, reason="llm_skip", source=self.name, coin_symbol=sym, hours=24.0
                )
                continue
            have_llm = verdict is not None
            result = self._scoring.score(inputs, have_llm_data=have_llm)
            await self._deps.db.execute(
                "INSERT INTO scores (coin_address, coin_symbol, bucket_name, profile, "
                "social, wallet, momentum, safety, acceleration, final_score, threshold, "
                "passed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    addr, sym, self.name, result.profile,
                    inputs.social, inputs.wallet, inputs.momentum, inputs.safety,
                    inputs.acceleration, result.final, result.threshold, 1 if result.passed else 0,
                ),
            )
            if not result.passed:
                continue
            hp = await self._honeypot.check(addr)
            if hp.is_honeypot:
                await self._deps.blacklist.add(
                    addr, reason="honeypot", source=self.name, coin_symbol=sym, permanent=True,
                )
                continue
            price = float(pair.get("priceUsd") or 0.0)
            if price <= 0:
                continue
            liq = float((pair.get("liquidity") or {}).get("usd") or 0.0)
            atr = await self._atr.compute(addr, price)
            stop_mult = float(self._cfg.get("atr_stop_mult", 2.0))
            tp_mult = float(self._cfg.get("atr_tp_mult", 5.0))
            stop_pct = -(atr * stop_mult) / price
            tp_pct = (atr * tp_mult) / price
            size_usd = await self.position_size_usd()
            if size_usd <= 0:
                continue
            req = await self._build_trade(
                addr, sym, "buy", price, size_usd * 0.5, liq,
                stop_loss_pct=stop_pct, take_profit_pct=tp_pct, atr=atr,
                extra={"llm_score": inputs.social / 10.0},
            )
            pid = await self._deps.executor.buy(req)
            self._log.info(
                "gem_buy {} score={:.2f} profile={} stop={:.3f} tp={:.3f} pos_id={}",
                sym, result.final, result.profile, stop_pct, tp_pct, pid,
            )
            opened += 1
        return opened

    async def manage_positions(self) -> int:
        """Exit on stop/TP/trailing.

        Returns:
            Number of positions closed this tick.
        """
        rows = await self._deps.db.fetchall(
            "SELECT * FROM positions WHERE bucket_name = ? AND status = 'OPEN'",
            (self.name,),
        )
        closed = 0
        for row in rows:
            try:
                detail = await self._dex.token_detail(row["coin_address"])
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
                row["coin_address"], row.get("coin_symbol") or "?", "sell", price,
                size_usd=float(row["size_usd"]),
                liquidity_usd=liquidity,
                position_id=row["id"],
            )
            pnl = await self._deps.executor.sell(req, reason)
            pnl_pct = pnl / max(float(row["size_usd"]), 1e-9)
            await self.on_close(row, pnl_pct)
            closed += 1
        return closed
