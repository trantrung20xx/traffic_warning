from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import AppConfig


def setup_logging(config: AppConfig) -> logging.Logger:
    log_dir = config.log_dir_path
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "camera_node.log"

    logger = logging.getLogger("traffic_camera_node")
    logger.setLevel(getattr(logging, config.logging.level, logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger
