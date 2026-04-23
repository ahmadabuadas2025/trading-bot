"""Scheduler that runs the LLM scan at 08:00 and 20:00 UTC.

Drives :class:`SocialCollector` to pre-populate social data, then
calls :class:`LLMClient` with a structured prompt, and persists the
verdicts into ``llm_scan_results``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from core.db import Database
from core.llm_client import LLMClient
from core.regime_client import RegimeClient
from core.social_collector import SocialCollector
from core.time_utils import TimeProvider

SYSTEM_PROMPT: str = (
    "You are a professional crypto analyst specializing in Solana meme coins. "
    "You will be given pre-collected social media data and on-chain metrics "
    "for each coin. Analyze the provided data and rank coins by pump "
    "potential. Respond ONLY with valid JSON, no other text whatsoever."
)


@dataclass
class Candidate:
    """A coin shortlist entry evaluated by the LLM.

    Attributes:
        address: Solana mint address.
        symbol: Ticker.
        bucket: Bucket that proposed the candidate.
        math_score: Score from the math layer.
        liquidity_usd: Pool liquidity.
        volume_1h_usd: 1h volume.
        age_hours: Age of the pair in hours.
        price_change_6h_pct: 6h price change fraction.
        price_change_1h_pct: 1h price change fraction.
    """

    address: str
    symbol: str
    bucket: str
    math_score: float
    liquidity_usd: float
    volume_1h_usd: float
    age_hours: float
    price_change_6h_pct: float
    price_change_1h_pct: float


class LLMScanner:
    """Drive the twice-daily LLM scan pipeline."""

    def __init__(
        self,
        db: Database,
        llm: LLMClient,
        social: SocialCollector,
        regime: RegimeClient,
        config: dict[str, Any],
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Create the scanner.

        Args:
            db: Connected :class:`Database`.
            llm: The :class:`LLMClient`.
            social: The :class:`SocialCollector`.
            regime: The :class:`RegimeClient`.
            config: The ``llm`` section from ``config.yaml``.
            time_provider: Optional injected clock.
        """
        self._db = db
        self._llm = llm
        self._social = social
        self._regime = regime
        self._cfg = config
        self._time = time_provider or TimeProvider()
        self._scan_hours = set(int(h) for h in config.get("scan_hours_utc", [8, 20]))
        self._ttl_hours = float(config.get("result_ttl_hours", 12))
        self._last_scan_hour: int | None = None

    def should_scan_now(self) -> bool:
        """Whether we are inside a scan hour we have not served yet.

        Returns:
            True if the scanner should run on this tick.
        """
        if not self._llm.enabled or not self._cfg.get("enabled", True):
            return False
        now = self._time.now()
        return now.hour in self._scan_hours and self._last_scan_hour != now.hour

    def build_prompt(self, candidates: list[Candidate], social_blobs: list[dict]) -> str:
        """Build the user prompt body for the LLM.

        Args:
            candidates: Pre-filtered coin shortlist.
            social_blobs: Pre-collected social dicts in the same order.

        Returns:
            The full user-message string.
        """
        snap = self._regime.current()
        coins: list[dict[str, Any]] = []
        for c, social in zip(candidates, social_blobs, strict=False):
            coins.append(
                {
                    "address": c.address,
                    "symbol": c.symbol,
                    "bucket": c.bucket,
                    "age_hours": c.age_hours,
                    "liquidity_usd": c.liquidity_usd,
                    "volume_1h_usd": c.volume_1h_usd,
                    "price_change_1h_pct": c.price_change_1h_pct,
                    "price_change_6h_pct": c.price_change_6h_pct,
                    "math_score": c.math_score,
                    "social": social,
                }
            )
        body = {
            "instructions": (
                "Rank the following Solana meme coins. Use the social data to "
                "assess community strength. Respond with ONLY the specified JSON."
            ),
            "schema": {
                "scan_time": "ISO timestamp",
                "market_summary": "one sentence on overall market",
                "rankings": [
                    {
                        "rank": 1,
                        "address": "contract address",
                        "symbol": "TICKER",
                        "llm_score": 8,
                        "verdict": "BUY|SKIP|WATCH",
                        "confidence": "HIGH|MEDIUM|LOW",
                        "social_buzz": "none|low|medium|high",
                        "kol_mentioned": False,
                        "red_flags": ["list of concerns or empty"],
                        "reason": "one sentence explanation",
                        "best_entry_window": "now|wait|missed",
                    }
                ],
            },
            "market_context": {
                "regime": snap.regime,
                "btc_change_24h": snap.btc_change_24h,
                "sol_change_24h": snap.sol_change_24h,
                "fear_greed": snap.fear_greed,
            },
            "coins": coins,
        }
        return json.dumps(body, ensure_ascii=False)

    async def run_scan(self, candidates: list[Candidate]) -> dict[str, Any]:
        """Execute a single LLM scan for a shortlist.

        Args:
            candidates: Coins to score.

        Returns:
            The raw LLM JSON response.
        """
        if not candidates:
            return {"scan_time": self._time.now_iso(), "rankings": []}

        # Collect social data in a bounded-concurrency fashion.
        socials: list[dict[str, Any]] = []
        for c in candidates:
            try:
                sd = await self._social.collect(c.address, c.symbol)
                socials.append(self._social.to_prompt_blob(sd))
            except Exception:  # noqa: BLE001
                socials.append({"data_quality": "none", "symbol": c.symbol})
            await asyncio.sleep(0)

        user_prompt = self.build_prompt(candidates, socials)
        result = await self._llm.chat_json(SYSTEM_PROMPT, user_prompt)

        await self._persist(candidates, result)
        self._last_scan_hour = self._time.now().hour
        return result

    def dry_run_prompt(self, candidates: list[Candidate]) -> str:
        """Build the prompt without calling the LLM.

        Args:
            candidates: Coins to score.

        Returns:
            The user prompt string.
        """
        return self.build_prompt(candidates, [{"symbol": c.symbol} for c in candidates])

    async def _persist(self, candidates: list[Candidate], result: dict[str, Any]) -> None:
        """Persist LLM verdicts into ``llm_scan_results``.

        Args:
            candidates: The shortlist sent to the LLM.
            result: The parsed LLM JSON response.
        """
        scan_time = self._time.now_iso()
        expires_at = (self._time.now() + timedelta(hours=self._ttl_hours)).isoformat()
        by_address = {c.address: c for c in candidates}
        for ranking in result.get("rankings") or []:
            addr = str(ranking.get("address") or "")
            cand = by_address.get(addr)
            if cand is None:
                continue
            verdict = str(ranking.get("verdict") or "WATCH").upper()
            llm_score = int(ranking.get("llm_score") or 0)
            red_flags = ranking.get("red_flags") or []
            approved = 1 if (verdict == "BUY" and llm_score >= 7 and not red_flags) else 0
            await self._db.execute(
                "INSERT INTO llm_scan_results "
                "(scan_time, coin_address, coin_symbol, bucket, llm_score, verdict, "
                "confidence, social_buzz, kol_mentioned, red_flags, reason, "
                "best_entry, math_score, market_regime, approved, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_time,
                    addr,
                    ranking.get("symbol") or cand.symbol,
                    cand.bucket,
                    llm_score,
                    verdict,
                    ranking.get("confidence"),
                    ranking.get("social_buzz"),
                    1 if ranking.get("kol_mentioned") else 0,
                    json.dumps(red_flags),
                    ranking.get("reason"),
                    ranking.get("best_entry_window"),
                    cand.math_score,
                    self._regime.current().regime,
                    approved,
                    expires_at,
                ),
            )

    async def latest_verdict(self, coin_address: str) -> dict[str, Any] | None:
        """Return the most recent non-expired verdict for a coin.

        Args:
            coin_address: Solana mint address.

        Returns:
            Row dict or ``None``.
        """
        now_iso = self._time.now_iso()
        return await self._db.fetchone(
            "SELECT * FROM llm_scan_results WHERE coin_address = ? "
            "AND expires_at > ? ORDER BY scan_time DESC LIMIT 1",
            (coin_address, now_iso),
        )
