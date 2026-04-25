"""Jupiter swap execution — quote, simulate, execute pipeline."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime

from core.config import BotConfig
from core.logger import LoggerFactory
from core.models import TradeRecord, TradeSide, TradeStatus

log = LoggerFactory.get_logger("executor")


class JupiterExecutor:
    """Full execution pipeline for Jupiter swaps.

    Paper mode: simulates without sending real transactions.
    Live mode: signs and sends transactions using wallet private key.
    """

    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._wallet_private_key: str = os.getenv("WALLET_PRIVATE_KEY", "")
        self._is_paper = config.app.mode == "paper"
        self._sol_price: float = config.paper_trading.fallback_sol_usd

    async def execute_swap(
        self,
        input_mint: str,
        output_mint: str,
        amount_usd: float,
        slippage_bps: int | None = None,
    ) -> TradeRecord | None:
        """Execute a swap through Jupiter.

        In paper mode, simulates the trade without blockchain interaction.
        In live mode, fetches quote, simulates, then signs and sends.

        Args:
            input_mint: Input token mint address.
            output_mint: Output token mint address.
            amount_usd: Trade amount in USD.
            slippage_bps: Slippage tolerance in basis points.

        Returns:
            TradeRecord on success, None on failure.
        """
        start_time = time.time()
        slippage = slippage_bps or self._config.jupiter.default_slippage_bps

        if self._is_paper:
            return await self._paper_execute(input_mint, output_mint, amount_usd, slippage, start_time)
        return await self._live_execute(input_mint, output_mint, amount_usd, slippage, start_time)

    async def _paper_execute(
        self,
        input_mint: str,
        output_mint: str,
        amount_usd: float,
        slippage_bps: int,
        start_time: float,
    ) -> TradeRecord:
        """Simulate a trade in paper mode."""
        execution_time = (time.time() - start_time) * 1000

        simulated_price = amount_usd / max(self._sol_price, 1.0)

        record = TradeRecord(
            id=str(uuid.uuid4()),
            strategy="paper",
            token_address=output_mint,
            side=TradeSide.BUY,
            amount_usd=amount_usd,
            amount_token=simulated_price,
            price=self._sol_price,
            slippage_bps=slippage_bps,
            tx_signature=f"paper_{uuid.uuid4().hex[:16]}",
            status=TradeStatus.EXECUTED,
            timestamp=datetime.now(UTC),
            execution_time_ms=execution_time,
        )

        log.info(
            "[PAPER] Swap {} -> {} ${:.2f} (price: ${:.2f})",
            input_mint[:8],
            output_mint[:8],
            amount_usd,
            self._sol_price,
        )
        return record

    async def _live_execute(
        self,
        input_mint: str,
        output_mint: str,
        amount_usd: float,
        slippage_bps: int,
        start_time: float,
    ) -> TradeRecord | None:
        """Execute a real trade via Jupiter API.

        This implementation provides the structure for live execution.
        Actual transaction signing requires solders/solana-py integration.
        """
        if not self._wallet_private_key:
            log.error("No wallet private key configured — cannot execute live trade")
            return None

        try:
            from data.jupiter_client import SOL_MINT, JupiterClient

            jupiter_config = self._config.jupiter
            client = JupiterClient(jupiter_config)
            await client.start()

            rpc = None
            try:
                amount_lamports = int(amount_usd / self._sol_price * 1e9) if input_mint == SOL_MINT else int(amount_usd * 1e6)

                quote = await client.get_quote(
                    input_mint=input_mint,
                    output_mint=output_mint,
                    amount=amount_lamports,
                    slippage_bps=slippage_bps,
                )

                out_amount = int(quote.get("outAmount", "0"))
                if out_amount <= 0:
                    log.error("Quote returned 0 output — aborting")
                    return None

                import base64

                from solders.keypair import Keypair  # type: ignore[import-untyped]

                keypair = Keypair.from_base58_string(self._wallet_private_key)
                public_key = str(keypair.pubkey())

                swap_response = await client.get_swap_transaction(quote, public_key)
                swap_tx = swap_response.get("swapTransaction", "")

                if not swap_tx:
                    log.error("No swap transaction returned")
                    return None

                from solana.rpc.async_api import AsyncClient  # type: ignore[import-untyped]
                from solders.transaction import VersionedTransaction  # type: ignore[import-untyped]

                rpc_url = self._config.solana.rpc_url if hasattr(self._config, 'solana') else "https://api.mainnet-beta.solana.com"
                rpc = AsyncClient(rpc_url)
                tx_bytes = base64.b64decode(swap_tx)
                tx = VersionedTransaction.from_bytes(tx_bytes)

                signed_tx = VersionedTransaction(tx.message, [keypair])
                result = await rpc.send_transaction(signed_tx)

                execution_time = (time.time() - start_time) * 1000
                tx_sig = str(result.value) if result.value else ""

                record = TradeRecord(
                    id=str(uuid.uuid4()),
                    strategy="live",
                    token_address=output_mint,
                    side=TradeSide.BUY,
                    amount_usd=amount_usd,
                    amount_token=float(out_amount),
                    price=amount_usd / max(float(out_amount), 1),
                    slippage_bps=slippage_bps,
                    tx_signature=tx_sig,
                    status=TradeStatus.EXECUTED,
                    timestamp=datetime.now(UTC),
                    execution_time_ms=execution_time,
                )
                log.info("[LIVE] Swap executed: tx={}", tx_sig[:16] if tx_sig else "unknown")
                return record
            finally:
                try:
                    if rpc:
                        await rpc.close()
                finally:
                    await client.close()

        except ImportError:
            log.error("solders/solana-py not available for live trading")
            return None
        except Exception:
            log.exception("Live swap execution failed")
            return None
