import logging
import os
import sys


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def setup_logging(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "[%(asctime)s] %(name)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    logger.propagate = True  # cho phép root logger (WebSocket handler) bắt được log
    return logger
