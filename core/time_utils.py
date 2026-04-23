"""Small UTC time helpers used across services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class TimeProvider:
    """Thin wrapper around :func:`datetime.utcnow` for testability."""

    def now(self) -> datetime:
        """Return the current UTC time as a timezone-aware datetime.

        Returns:
            Current UTC time.
        """
        return datetime.now(tz=UTC)

    def now_iso(self) -> str:
        """Return the current UTC time formatted as ISO-8601.

        Returns:
            ``YYYY-MM-DDTHH:MM:SS+00:00`` string.
        """
        return self.now().isoformat()

    def add(self, delta_seconds: float) -> datetime:
        """Return the current UTC time plus an offset in seconds.

        Args:
            delta_seconds: Seconds to add (may be negative).

        Returns:
            Shifted UTC time.
        """
        return self.now() + timedelta(seconds=delta_seconds)
