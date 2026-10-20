"""Structured logging for AutoSwarm system."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_dir: Path | None = None, level: str = "INFO") -> None:
    """Configure loguru for AutoSwarm with file + console output."""
    logger.remove()

    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - {message}",
        colorize=True,
    )

    if log_dir:
        from datetime import date
        day_dir = log_dir / date.today().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(day_dir / "opc.log"),
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            rotation="50 MB",
            retention="30 days",
        )
