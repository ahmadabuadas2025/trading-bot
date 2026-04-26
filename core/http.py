"""Resilient async HTTP client.

Wraps :mod:`aiohttp` with:

* Exponential backoff (configurable base/max, max retries).
* A circuit breaker per host: N consecutive failures open the breaker
  and return cached-failures until a cooldown elapses.
* JSON parsing convenience.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import aiohttp


class CircuitOpenError(RuntimeError):
    """Raised when a host-level circuit breaker is open."""


@dataclass
class _Breaker:
    """Per-host breaker state.

    Attributes:
        failures: Current consecutive failure count.
        open_until: Monotonic timestamp until which the breaker is open.
    """

    failures: int = 0
    open_until: float = 0.0


class HttpClient:
    """Resilient async HTTP client shared across services."""

    def __init__(
        self,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 10.0,
        circuit_breaker_failures: int = 5,
        circuit_breaker_cooldown_seconds: float = 60.0,
        default_timeout_seconds: float = 20.0,
        user_agent: str = "SolanaTradingBot/0.1",
    ) -> None:
        """Create the client.

        Args:
            max_retries: Attempts before giving up.
            backoff_base_seconds: Base delay for exponential backoff.
            backoff_max_seconds: Cap on a single backoff sleep.
            circuit_breaker_failures: Failures that open the breaker.
            circuit_breaker_cooldown_seconds: Open-state duration.
            default_timeout_seconds: Per-request timeout.
            user_agent: Default User-Agent header.
        """
        self._max_retries = max_retries
        self._base = backoff_base_seconds
        self._cap = backoff_max_seconds
        self._cb_fail = circuit_breaker_failures
        self._cb_cooldown = circuit_breaker_cooldown_seconds
        self._timeout = aiohttp.ClientTimeout(total=default_timeout_seconds)
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._session: aiohttp.ClientSession | None = None
        self._breakers: dict[str, _Breaker] = {}

    async def start(self) -> None:
        """Lazily open the underlying :class:`aiohttp.ClientSession`."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout, headers=self._headers)

    async def close(self) -> None:
        """Close the underlying session if open."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    def _host_of(self, url: str) -> str:
        """Return the host portion of a URL for breaker bookkeeping.

        Args:
            url: Absolute URL.

        Returns:
            ``scheme://host`` prefix for the URL.
        """
        try:
            parts = url.split("/")
            return f"{parts[0]}//{parts[2]}"
        except IndexError:
            return url

    def _check_breaker(self, host: str) -> None:
        """Raise :class:`CircuitOpenError` when the breaker is open.

        Args:
            host: Host key.
        """
        b = self._breakers.get(host)
        if b is None:
            return
        if b.open_until > time.monotonic():
            raise CircuitOpenError(f"Circuit open for {host}")
        if b.open_until and b.open_until <= time.monotonic():
            b.failures = 0
            b.open_until = 0.0

    def _record_success(self, host: str) -> None:
        """Reset the breaker for a host on success.

        Args:
            host: Host key.
        """
        b = self._breakers.get(host)
        if b is not None:
            b.failures = 0
            b.open_until = 0.0

    def _record_failure(self, host: str) -> None:
        """Increment the breaker and possibly open it.

        Args:
            host: Host key.
        """
        b = self._breakers.setdefault(host, _Breaker())
        b.failures += 1
        if b.failures >= self._cb_fail:
            b.open_until = time.monotonic() + self._cb_cooldown

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        json_body: Any | None = None,
    ) -> Any:
        """Perform an HTTP call and parse JSON with retries and CB.

        Args:
            method: HTTP method (``GET``/``POST``).
            url: Absolute URL.
            params: Optional query-string mapping.
            headers: Optional extra headers.
            json_body: Optional JSON body.

        Returns:
            Decoded JSON payload.

        Raises:
            CircuitOpenError: If the circuit is open for the host.
            aiohttp.ClientError: If all retries fail.
        """
        await self.start()
        assert self._session is not None
        host = self._host_of(url)
        self._check_breaker(host)
        last_err: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._session.request(
                    method, url, params=params, headers=headers, json=json_body
                ) as resp:
                    if resp.status in (429, 500, 502, 503, 504):
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status,
                            message=f"retryable {resp.status}",
                        )
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
                self._record_success(host)
                return data
            except Exception as err:  # noqa: BLE001
                last_err = err
                if attempt == self._max_retries:
                    self._record_failure(host)
                    raise
                sleep_for = min(self._cap, self._base * (2 ** (attempt - 1)))
                await asyncio.sleep(sleep_for)
        if last_err:
            raise last_err
        raise RuntimeError("unreachable")
