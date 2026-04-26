"""Structured logging setup using loguru."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


class LoggerFactory:
    """Factory for creating structured loggers with file and console output."""

    _initialized: bool = False

    @classmethod
    def setup(cls, log_level: str = "INFO", log_path: str = "logs/bot.log") -> None:
        """Initialize logging with structured JSON file output and console output."""
        if cls._initialized:
            return

        logger.remove()

        logger.add(
            sys.stderr,
            level=log_level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{extra[module]}</cyan> | "
                "<level>{message}</level>"
            ),
            backtrace=True,
            diagnose=False,
        )

        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            str(log_file),
            level=log_level,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[module]} | {message}",
            rotation="10 MB",
            retention="7 days",
            serialize=True,
            backtrace=True,
            diagnose=False,
        )

        cls._initialized = True

    @classmethod
    def get_logger(cls, module: str = "bot") -> logger:  # type: ignore[type-arg]
        """Return a logger bound to the given module name."""
        return logger.bind(module=module)
