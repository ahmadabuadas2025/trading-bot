"""Honeypot / scam-risk check.

Tries ``honeypot.is`` first and falls back to ``rugcheck.xyz``. Any
unrecoverable failure reports *not* honeypot so a temporary API outage
does not globally block the bot; services always pair this with other
safety checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.http import HttpClient


@dataclass
class HoneypotResult:
    """Outcome of a honeypot check.

    Attributes:
        is_honeypot: True if any source flagged the token.
        risk_score: 0-100 risk estimate (higher is worse), or ``None``.
        source: The source that produced the verdict.
        raw_reason: Free-form reason from the source (when available).
    """

    is_honeypot: bool
    risk_score: float | None
    source: str
    raw_reason: str | None = None


class HoneypotChecker:
    """Best-effort honeypot classifier."""

    HONEYPOT_URL: str = "https://api.honeypot.is/v2/IsHoneypot"
    RUGCHECK_URL: str = "https://api.rugcheck.xyz/v1/tokens"

    def __init__(self, http: HttpClient) -> None:
        """Create the checker.

        Args:
            http: Shared :class:`HttpClient`.
        """
        self._http = http

    async def check(self, address: str) -> HoneypotResult:
        """Run the layered honeypot check.

        Args:
            address: Solana mint address.

        Returns:
            A :class:`HoneypotResult`.
        """
        try:
            data = await self._http.request_json(
                "GET", self.HONEYPOT_URL, params={"address": address, "chainID": "solana"}
            )
            flags = (data or {}).get("honeypotResult") or {}
            if flags.get("isHoneypot"):
                return HoneypotResult(True, 100.0, "honeypot.is", flags.get("reason"))
        except Exception:  # noqa: BLE001
            pass
        try:
            data = await self._http.request_json(
                "GET", f"{self.RUGCHECK_URL}/{address}/report"
            )
            score = float((data or {}).get("score") or 0.0)
            risks = (data or {}).get("risks") or []
            is_hp = any((r.get("level") == "danger") for r in risks) or score > 80.0
            reason = risks[0].get("name") if risks else None
            return HoneypotResult(is_hp, score, "rugcheck.xyz", reason)
        except Exception:  # noqa: BLE001
            return HoneypotResult(False, None, "unknown", "no source reachable")
