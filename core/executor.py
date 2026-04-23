"""Trade executor.

Two implementations behind a small abstract interface:

* :class:`PaperExecutor` applies the :class:`SlippageModel` to fake
  fills and updates bucket balances in SQLite.
* :class:`LiveExecutor` calls :class:`JupiterClient` to build a real
  swap transaction, then warns about missing signer wiring (it is
  intentionally conservative and refuses to send without a wallet).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from clients.jupiter import JupiterClient
from core.db import Database
from core.slippage_model import ExecutionResult, SlippageModel


@dataclass
class TradeRequest:
    """Parameters for a single buy or sell.

    Attributes:
        bucket: Owning bucket key.
        coin_address: Solana mint address.
        coin_symbol: Ticker.
        side: ``buy`` or ``sell``.
        market_price: Observed mid price.
        size_usd: USD notional requested.
        liquidity_usd: Pool liquidity at quote time.
        position_id: Optional open position id for sells.
        stop_loss_pct: Optional stop-loss to store with the new position.
        take_profit_pct: Optional take-profit to store with the new position.
        atr: Optional ATR to store with the new position.
        extra: Free-form JSON metadata.
    """

    bucket: str
    coin_address: str
    coin_symbol: str
    side: str
    market_price: float
    size_usd: float
    liquidity_usd: float
    position_id: int | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    atr: float | None = None
    extra: dict[str, Any] | None = None


class Executor(ABC):
    """Abstract executor interface."""

    @abstractmethod
    async def buy(self, req: TradeRequest) -> int:
        """Open or scale into a position.

        Args:
            req: Trade parameters.

        Returns:
            The row id of the opened position.
        """

    @abstractmethod
    async def sell(self, req: TradeRequest, reason: str) -> float:
        """Close a position and record realised P&L.

        Args:
            req: Trade parameters.
            reason: Human-readable close reason.

        Returns:
            Realised P&L in USD.
        """


class PaperExecutor(Executor):
    """Simulated executor for paper mode."""

    def __init__(
        self,
        db: Database,
        slippage: SlippageModel,
        sol_price_usd: float | None = None,
    ) -> None:
        """Create the paper executor.

        Args:
            db: Connected :class:`Database`.
            slippage: The :class:`SlippageModel`.
            sol_price_usd: Optional live SOL price for fee conversion.
        """
        self._db = db
        self._slippage = slippage
        self._sol_price = sol_price_usd

    def set_sol_price(self, sol_price_usd: float | None) -> None:
        """Update the cached SOL price used for fee conversion.

        Args:
            sol_price_usd: Current SOL/USD price.
        """
        self._sol_price = sol_price_usd

    async def _adjust_bucket(self, bucket: str, delta_usd: float) -> None:
        """Credit or debit a bucket's balance.

        Args:
            bucket: Bucket key.
            delta_usd: USD delta (negative for debits).
        """
        await self._db.execute(
            "UPDATE fund_buckets SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE bucket_name = ?",
            (delta_usd, bucket),
        )

    async def buy(self, req: TradeRequest) -> int:
        """Simulate a buy.

        Args:
            req: Trade parameters.

        Returns:
            The row id of the new position.
        """
        res = self._slippage.simulate(
            "buy",
            market_price=req.market_price,
            trade_size_usd=req.size_usd,
            liquidity_usd=req.liquidity_usd,
            sol_price_usd=self._sol_price,
        )
        await self._adjust_bucket(req.bucket, -req.size_usd)
        position_id = await self._db.execute(
            "INSERT INTO positions "
            "(bucket_name, coin_address, coin_symbol, entry_price, size_tokens, "
            "size_usd, fees_paid, stop_loss_pct, take_profit_pct, atr, peak_price, "
            "status, extra_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)",
            (
                req.bucket,
                req.coin_address,
                req.coin_symbol,
                res.executed_price,
                res.size_tokens,
                req.size_usd,
                res.fee_usd,
                req.stop_loss_pct,
                req.take_profit_pct,
                req.atr,
                res.executed_price,
                json.dumps(req.extra or {}),
            ),
        )
        await self._record_trade(req, res, position_id, mode="paper")
        return position_id

    async def sell(self, req: TradeRequest, reason: str) -> float:
        """Simulate a sell that closes ``position_id`` fully.

        Args:
            req: Trade parameters (``position_id`` required).
            reason: Human-readable close reason.

        Returns:
            Realised P&L in USD.
        """
        if req.position_id is None:
            raise ValueError("Sell requires position_id")
        pos = await self._db.fetchone(
            "SELECT * FROM positions WHERE id = ?", (req.position_id,)
        )
        if pos is None:
            raise ValueError(f"Unknown position {req.position_id}")
        size_tokens = float(pos["size_tokens"])
        size_usd = size_tokens * req.market_price
        res = self._slippage.simulate(
            "sell",
            market_price=req.market_price,
            trade_size_usd=size_usd,
            liquidity_usd=req.liquidity_usd,
            sol_price_usd=self._sol_price,
        )
        gross = size_tokens * res.executed_price
        net = max(gross - res.fee_usd, 0.0)
        pnl = net - float(pos["size_usd"])
        pnl_pct = pnl / max(float(pos["size_usd"]), 1e-9)
        await self._adjust_bucket(req.bucket, net)
        await self._db.execute(
            "UPDATE positions SET status = 'CLOSED', closed_at = CURRENT_TIMESTAMP, "
            "exit_price = ?, pnl_usd = ?, pnl_pct = ?, close_reason = ? WHERE id = ?",
            (res.executed_price, pnl, pnl_pct, reason, req.position_id),
        )
        await self._record_trade(req, res, req.position_id, mode="paper")
        return pnl

    async def _record_trade(
        self,
        req: TradeRequest,
        res: ExecutionResult,
        position_id: int,
        mode: str,
    ) -> None:
        """Append a row to ``trades``.

        Args:
            req: Trade request.
            res: Fill result.
            position_id: Owning position id.
            mode: ``paper`` or ``live``.
        """
        await self._db.execute(
            "INSERT INTO trades "
            "(position_id, bucket_name, coin_address, coin_symbol, side, mode, "
            "market_price, executed_price, size_tokens, size_usd, slippage_pct, fee_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                position_id,
                req.bucket,
                req.coin_address,
                req.coin_symbol,
                req.side,
                mode,
                res.market_price,
                res.executed_price,
                res.size_tokens,
                res.size_usd,
                res.slippage_pct,
                res.fee_usd,
            ),
        )


class LiveExecutor(Executor):
    """Live executor: builds Jupiter swap transactions.

    The actual signing + sendTransaction step is intentionally left
    as a ``# TODO`` so accidental real trades do not ship until a
    human wires a Solana signer library (e.g. solders + solana-py).
    """

    def __init__(
        self,
        db: Database,
        jupiter: JupiterClient,
        wallet_private_key: str | None,
    ) -> None:
        """Create the live executor.

        Args:
            db: Connected :class:`Database`.
            jupiter: Jupiter client for quotes and swaps.
            wallet_private_key: Solana signer key (base58).
        """
        self._db = db
        self._jup = jupiter
        self._key = wallet_private_key
        if not wallet_private_key:
            raise RuntimeError("Live mode requires WALLET_PRIVATE_KEY in .env")

    async def buy(self, req: TradeRequest) -> int:
        """Build a live-swap transaction for a buy. See module docstring.

        Args:
            req: Trade parameters.

        Returns:
            The row id of the pending position.
        """
        # TODO: derive public key from secret, sign, send, confirm.
        raise NotImplementedError(
            "LiveExecutor.buy requires a Solana signer integration. "
            "Wire up `solders`/`solana-py` before enabling live trading."
        )

    async def sell(self, req: TradeRequest, reason: str) -> float:
        """Build a live-swap transaction for a sell. See module docstring.

        Args:
            req: Trade parameters.
            reason: Human-readable close reason.

        Returns:
            Realised P&L in USD.
        """
        # TODO: derive public key from secret, sign, send, confirm.
        raise NotImplementedError(
            "LiveExecutor.sell requires a Solana signer integration. "
            "Wire up `solders`/`solana-py` before enabling live trading."
        )
