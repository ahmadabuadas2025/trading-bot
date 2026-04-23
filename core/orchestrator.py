"""Main trading loop that wires every service together.

Each bucket runs on its own asynchronous timer; the orchestrator also
refreshes the market regime, runs the twice-daily LLM scan, and runs
the safety monitor at the configured cadence.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from core.llm_scanner import Candidate, LLMScanner
from core.logger import LoggerFactory
from core.regime_client import RegimeClient
from core.safety_monitor import SafetyMonitor
from services.base_bucket import BaseBucket


@dataclass
class BucketRunner:
    """A bucket wrapped with its scan/manage intervals.

    Attributes:
        service: The :class:`BaseBucket` instance.
        scan_interval: Seconds between ``scan_and_enter`` calls.
        manage_interval: Seconds between ``manage_positions`` calls.
    """

    service: BaseBucket
    scan_interval: float
    manage_interval: float


class Orchestrator:
    """Run every service concurrently inside one event loop."""

    def __init__(
        self,
        buckets: list[BucketRunner],
        regime: RegimeClient,
        safety: SafetyMonitor,
        llm_scanner: LLMScanner,
        logger: LoggerFactory,
        config: dict[str, Any],
    ) -> None:
        """Create the orchestrator.

        Args:
            buckets: All active bucket runners.
            regime: Regime client.
            safety: Safety monitor.
            llm_scanner: LLM scanner.
            logger: Logger factory.
            config: Full parsed config tree.
        """
        self._buckets = buckets
        self._regime = regime
        self._safety = safety
        self._llm = llm_scanner
        self._log = logger.get("orchestrator")
        self._cfg = config
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        """Ask every loop to stop on its next iteration."""
        self._stop.set()

    async def _regime_loop(self) -> None:
        """Refresh the market regime on a timer."""
        interval = int(self._cfg.get("regime", {}).get("refresh_minutes", 30)) * 60
        while not self._stop.is_set():
            try:
                snap = await self._regime.refresh()
                self._log.info(
                    "regime={} btc={:.2%} sol={:.2%} fg={}",
                    snap.regime, snap.btc_change_24h, snap.sol_change_24h, snap.fear_greed,
                )
            except Exception as err:  # noqa: BLE001
                self._log.warning("regime refresh failed: {}", err)
            await self._wait(interval)

    async def _safety_loop(self) -> None:
        """Tick the safety monitor on a timer."""
        interval = int(self._cfg.get("safety", {}).get("check_interval_seconds", 300))
        while not self._stop.is_set():
            try:
                await self._safety.tick()
            except Exception as err:  # noqa: BLE001
                self._log.warning("safety tick failed: {}", err)
            await self._wait(interval)

    async def _llm_loop(self) -> None:
        """Run the LLM scan on its twice-daily schedule."""
        while not self._stop.is_set():
            try:
                if self._llm.should_scan_now():
                    candidates: list[Candidate] = []
                    await self._llm.run_scan(candidates)
                    self._log.info("LLM scan completed")
            except Exception as err:  # noqa: BLE001
                self._log.warning("LLM scan failed: {}", err)
            await self._wait(600)

    async def _bucket_scan_loop(self, runner: BucketRunner) -> None:
        """Run ``scan_and_enter`` on a timer for one bucket.

        Args:
            runner: The bucket runner to drive.
        """
        while not self._stop.is_set():
            try:
                opened = await runner.service.scan_and_enter()
                if opened:
                    self._log.info("{} opened {}", runner.service.name, opened)
            except Exception as err:  # noqa: BLE001
                self._log.warning("{} scan failed: {}", runner.service.name, err)
            await self._wait(runner.scan_interval)

    async def _bucket_manage_loop(self, runner: BucketRunner) -> None:
        """Run ``manage_positions`` on a timer for one bucket.

        Args:
            runner: The bucket runner to drive.
        """
        while not self._stop.is_set():
            try:
                closed = await runner.service.manage_positions()
                if closed:
                    self._log.info("{} closed {}", runner.service.name, closed)
            except Exception as err:  # noqa: BLE001
                self._log.warning("{} manage failed: {}", runner.service.name, err)
            await self._wait(runner.manage_interval)

    async def _wait(self, seconds: float) -> None:
        """Sleep or exit early when :meth:`request_stop` is called.

        Args:
            seconds: Sleep duration in seconds.
        """
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return

    async def run(self) -> None:
        """Launch every loop concurrently and wait for a stop signal."""
        self._log.info("orchestrator starting with {} buckets", len(self._buckets))
        # Kick the regime once so buckets get multipliers on the first tick.
        try:
            await self._regime.refresh()
        except Exception as err:  # noqa: BLE001
            self._log.warning("initial regime refresh failed: {}", err)
        tasks = [
            asyncio.create_task(self._regime_loop()),
            asyncio.create_task(self._safety_loop()),
            asyncio.create_task(self._llm_loop()),
        ]
        for runner in self._buckets:
            tasks.append(asyncio.create_task(self._bucket_scan_loop(runner)))
            tasks.append(asyncio.create_task(self._bucket_manage_loop(runner)))
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._log.info("orchestrator stopped")
