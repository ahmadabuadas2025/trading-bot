"""Structured logging via loguru.

Provides a single :class:`LoggerFactory` that configures loguru once
(to both the console and a rotating file) and hands out named
sub-loggers via :meth:`LoggerFactory.get`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


class LoggerFactory:
    """Configure loguru once and hand out named sub-loggers."""

    _configured: bool = False

    def __init__(self, log_path: str | Path, level: str = "INFO") -> None:
        """Create a factory.

        Args:
            log_path: Path to the rotating log file.
            level: Minimum log level for both sinks.
        """
        self._log_path = Path(log_path)
        self._level = level.upper()

    def configure(self) -> None:
        """Install the console and file sinks exactly once."""
        if LoggerFactory._configured:
            return
        logger.remove()
        console_format = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[component]}</cyan> | {message}"
        )
        file_format = (
            "{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level: <8} | "
            "{extra[component]} | {message}"
        )
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.configure(extra={"component": "app"})
        logger.add(
            sys.stderr,
            level=self._level,
            format=console_format,
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )
        logger.add(
            self._log_path,
            level=self._level,
            format=file_format,
            rotation="20 MB",
            retention="14 days",
            compression="zip",
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )
        LoggerFactory._configured = True

    def get(self, component: str):
        """Return a logger bound to a component name.

        Args:
            component: Short component tag included in every record.

        Returns:
            A loguru logger with ``component`` bound in ``extra``.
        """
        self.configure()
        return logger.bind(component=component)
