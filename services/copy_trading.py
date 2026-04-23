"""COPY_TRADER bucket: mirror the top active Solana wallets.

Real-time wallet monitoring uses Helius (primary) or Birdeye polling
(fallback). If no keys are present a :class:`MockWalletProvider`
produces fake buys so paper mode can exercise the pipeline.
"""

from __future__ import annotations

import random
from typing import Any

from clients.birdeye import BirdeyeClient
from clients.dexscreener import DexScreenerClient
from clients.helius import HeliusClient
from services.base_bucket import BaseBucket, BucketDeps


class MockWalletProvider:
    """Emit fake wallet activity for paper testing without keys."""

    def __init__(self) -> None:
        """Create the mock provider."""
        self._counter: int = 0

    async def poll_buys(self) -> list[dict[str, Any]]:
        """Yield zero or one fake buy per call.

        Returns:
            A list with one fake buy ~30% of the time, else empty.
        """
        self._counter += 1
        if random.random() > 0.3:
            return []
        return [
            {
                "wallet": "MockWalletXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
                "coin_address": f"MockCoin{self._counter:06d}XXXXXXXXXXXXXXXX",
                "coin_symbol": f"MOCK{self._counter}",
                "size_usd": 500.0,
                "wallet_win_rate": 0.6,
                "wallet_trades_7d": 30,
            }
        ]


class CopyTradingService(BaseBucket):
    """Mirror top wallets with a short delay and own exit rules."""

    name: str = "COPY_TRADER"

    def __init__(
        self,
        deps: BucketDeps,
        bucket_cfg: dict[str, Any],
        helius: HeliusClient,
        birdeye: BirdeyeClient,
        dex: DexScreenerClient,
        mock_wallets: MockWalletProvider | None = None,
    ) -> None:
        """Create the service.

        Args:
            deps: Shared deps.
            bucket_cfg: Bucket config block.
            helius: Helius client.
            birdeye: Birdeye client.
            dex: DexScreener client (for price + liquidity).
            mock_wallets: Optional paper-mode mock provider.
        """
        super().__init__(deps, bucket_cfg)
        self._helius = helius
        self._birdeye = birdeye
        self._dex = dex
        self._mock = mock_wallets or MockWalletProvider()

    async def _poll_buys(self) -> list[dict[str, Any]]:
        """Return a flat list of wallet-buy signals.

        Returns:
            Normalised dicts with coin address + size fields.
        """
        wallets = await self._deps.db.fetchall(
            "SELECT address, win_rate, trades_7d FROM wallets WHERE enabled = 1 LIMIT 10"
        )
        if not wallets:
            return await self._mock.poll_buys()
        signals: list[dict[str, Any]] = []
        for w in wallets:
            addr = w["address"]
            txs: list[dict[str, Any]] = []
            if self._helius.available:
                try:
                    txs = await self._helius.wallet_transactions(addr, limit=5)
                except Exception:  # noqa: BLE001
                    txs = []
            if not txs:
                try:
                    txs = await self._birdeye.wallet_tx_list(addr, limit=5)
                except Exception:  # noqa: BLE001
                    txs = []
            for tx in txs:
                signals.append(
                    {
                        "wallet": addr,
                        "coin_address": tx.get("to") or tx.get("buyToken") or "",
                        "coin_symbol": tx.get("symbol") or "?",
                        "size_usd": float(tx.get("usd") or 200.0),
                        "wallet_win_rate": float(w.get("win_rate") or 0.55),
                        "wallet_trades_7d": int(w.get("trades_7d") or 20),
                    }
                )
        return [s for s in signals if s.get("coin_address")]

    def _confidence(self, signal: dict[str, Any]) -> float:
        """Compute the ``wallet_confidence`` capped at 0.5.

        Args:
            signal: Normalised wallet-buy signal.

        Returns:
            Confidence factor in ``[0.0, 0.5]``.
        """
        wr = float(signal.get("wallet_win_rate") or 0.0)
        t7d = float(signal.get("wallet_trades_7d") or 0.0)
        return min(wr * (t7d / 20.0), 0.5)

    async def scan_and_enter(self) -> int:
        """Mirror fresh wallet buys through the safety gate.

        Returns:
            Number of positions opened this tick.
        """
        if not await self.enabled():
            return 0
        opened = 0
        for sig in await self._poll_buys():
            addr = sig["coin_address"]
            sym = sig.get("coin_symbol") or "?"
            ok, reason = await self.can_open(addr)
            if not ok:
                self._log.debug("skip {} ({})", sym, reason)
                continue
            conf = self._confidence(sig)
            size_usd = max(float(sig.get("size_usd") or 0.0) * conf, 0.0)
            regime_mult = self._deps.regime.get_multiplier(self.name)
            size_usd *= regime_mult
            bal = await self.balance()
            size_usd = min(size_usd, bal * 0.10)
            if size_usd < 1.0:
                continue
            price, liquidity = await self._price_and_liq(addr)
            if price <= 0:
                continue
            req = await self._build_trade(
                addr, sym, "buy", price, size_usd, liquidity,
                stop_loss_pct=float(self._cfg.get("stop_loss_pct", -0.12)),
                take_profit_pct=float(self._cfg.get("take_profit_pct", 0.35)),
                extra={"mirrored_wallet": sig["wallet"], "confidence": conf},
            )
            pid = await self._deps.executor.buy(req)
            self._log.info(
                "mirror {} wallet={} conf={:.2f} size_usd={:.2f} pos_id={}",
                sym, sig["wallet"], conf, size_usd, pid,
            )
            opened += 1
        return opened

    async def _price_and_liq(self, address: str) -> tuple[float, float]:
        """Fetch spot price + liquidity via DexScreener.

        Args:
            address: Solana mint address.

        Returns:
            Tuple of ``(price_usd, liquidity_usd)``.
        """
        try:
            detail = await self._dex.token_detail(address)
        except Exception:  # noqa: BLE001
            detail = None
        if detail is None:
            return (0.0, 0.0)
        price = float(detail.get("priceUsd") or 0.0)
        liq = float((detail.get("liquidity") or {}).get("usd") or 0.0)
        return (price, liq)

    async def manage_positions(self) -> int:
        """Stop / TP / trailing exit check.

        Returns:
            Number of positions closed this tick.
        """
        rows = await self._deps.db.fetchall(
            "SELECT * FROM positions WHERE bucket_name = ? AND status = 'OPEN'",
            (self.name,),
        )
        closed = 0
        for row in rows:
            price, liquidity = await self._price_and_liq(row["coin_address"])
            if price <= 0:
                continue
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
