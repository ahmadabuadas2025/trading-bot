"""NEW_LISTING bucket: snipe brand new Solana listings.

Polls DexScreener's latest token profiles endpoint every few minutes
and enters on the highest-conviction survivors. LLM verdicts, when
available, boost scores or blacklist the coin outright.
"""

from __future__ import annotations

from typing import Any

from clients.dexscreener import DexScreenerClient
from core.llm_scanner import LLMScanner
from core.scoring_engine import ScoreInputs, ScoringEngine
from services.base_bucket import BaseBucket, BucketDeps
from utils.honeypot import HoneypotChecker


class NewListingService(BaseBucket):
    """Snipe brand-new tokens within a 30-minute window."""

    name: str = "NEW_LISTING"

    def __init__(
        self,
        deps: BucketDeps,
        bucket_cfg: dict[str, Any],
        dex: DexScreenerClient,
        scoring: ScoringEngine,
        llm_scanner: LLMScanner,
        honeypot: HoneypotChecker,
    ) -> None:
        """Create the service.

        Args:
            deps: Shared dependency bundle.
            bucket_cfg: Bucket config block.
            dex: DexScreener client.
            scoring: Scoring engine.
            llm_scanner: LLM scanner for verdict lookup.
            honeypot: Honeypot checker.
        """
        super().__init__(deps, bucket_cfg)
        self._dex = dex
        self._scoring = scoring
        self._llm = llm_scanner
        self._honeypot = honeypot

    async def _gather(self) -> list[dict[str, Any]]:
        """Pull the newest token profiles from DexScreener.

        Returns:
            A list of pair dicts with token-profile seed data.
        """
        try:
            profiles = await self._dex.latest_profiles()
        except Exception:  # noqa: BLE001
            return []
        entries: list[dict[str, Any]] = []
        for profile in profiles[:25]:
            addr = profile.get("tokenAddress")
            if not addr:
                continue
            try:
                detail = await self._dex.token_detail(addr)
            except Exception:  # noqa: BLE001
                detail = None
            if detail is not None:
                entries.append(detail)
        return entries

    def _passes_prefilter(self, pair: dict[str, Any]) -> bool:
        """NEW_LISTING pre-filter.

        Args:
            pair: DexScreener pair dict.

        Returns:
            True if it survives the pre-filter.
        """
        entry = self._cfg.get("entry") or {}
        liq = float((pair.get("liquidity") or {}).get("usd") or 0.0)
        vol = float((pair.get("volume") or {}).get("h1") or 0.0)
        age_ms = int(pair.get("pairCreatedAt") or 0)
        max_age_min = float(entry.get("max_age_minutes", 30))
        if liq < float(entry.get("min_liq_usd", 5000)):
            return False
        if vol <= 0:
            return False
        if age_ms:
            age_min = (self._deps.time.now().timestamp() - age_ms / 1000.0) / 60.0
            if age_min > max_age_min:
                return False
        return True

    async def scan_and_enter(self) -> int:
        """Score and open positions for qualifying listings.

        Returns:
            Number of positions opened this tick.
        """
        if not await self.enabled():
            return 0
        opened = 0
        for pair in await self._gather():
            if not self._passes_prefilter(pair):
                continue
            base = pair.get("baseToken") or {}
            addr = base.get("address")
            sym = base.get("symbol") or "?"
            if not addr:
                continue
            ok, reason = await self.can_open(addr)
            if not ok:
                self._log.debug("skip {} ({})", sym, reason)
                continue
            liq = float((pair.get("liquidity") or {}).get("usd") or 0.0)
            vol = float((pair.get("volume") or {}).get("h1") or 0.0)
            vol_5m = float((pair.get("volume") or {}).get("m5") or 0.0)
            change_1h = float((pair.get("priceChange") or {}).get("h1") or 0.0) / 100.0
            ratio = vol / max(liq, 1.0)
            inputs = ScoreInputs(
                momentum=self._scoring.momentum_score(change_1h, ratio),
                safety=self._scoring.safety_score(liq, ratio),
                acceleration=self._scoring.acceleration_score(vol_5m, vol, change_1h, ratio),
            )
            verdict = await self._llm.latest_verdict(addr)
            have_llm = verdict is not None
            if verdict is not None:
                inputs.social = float(verdict.get("llm_score") or 0) * 10.0
                inputs.wallet = 100.0 if verdict.get("kol_mentioned") else 30.0
                if (verdict.get("verdict") or "").upper() == "SKIP":
                    await self._deps.blacklist.add(
                        addr, reason="llm_skip", source=self.name,
                        coin_symbol=sym, hours=24.0,
                    )
                    continue
            result = self._scoring.score(inputs, have_llm_data=have_llm)
            if have_llm and result.passed:
                # Spec calls for +100 boost on approved LLM coins.
                result.final += 100.0
            if not result.passed:
                continue
            hp = await self._honeypot.check(addr)
            if hp.is_honeypot:
                await self._deps.blacklist.add(
                    addr, reason="honeypot", source=self.name,
                    coin_symbol=sym, permanent=True,
                )
                continue
            price = float(pair.get("priceUsd") or 0.0)
            if price <= 0:
                continue
            size_usd = await self.position_size_usd()
            if size_usd <= 0:
                continue
            req = await self._build_trade(
                addr, sym, "buy", price, size_usd, liq,
                stop_loss_pct=float(self._cfg.get("stop_loss_pct", -0.20)),
                take_profit_pct=float(self._cfg.get("take_profit_pct", 0.60)),
            )
            pid = await self._deps.executor.buy(req)
            self._log.info(
                "listing_buy {} score={:.2f} profile={} pos_id={}",
                sym, result.final, result.profile, pid,
            )
            opened += 1
        return opened

    async def manage_positions(self) -> int:
        """Exit on stop/TP (no trailing per spec).

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
