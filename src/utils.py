import io
import logging
import os
import sys


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _safe_console_stream():
    """Trên Windows build console=False, sys.stdout có thể None hoặc dùng
    encoding hệ thống (cp1252) không encode được ký tự Unicode (→, ✓...),
    làm crash toàn bộ logging → crash pipeline. Ép UTF-8 với errors='replace'
    để không bao giờ chết vì 1 ký tự lạ trong log."""
    stream = sys.stdout
    if stream is None:
        return io.StringIO()  # windowed mode không có console thật — log rơi vào hố đen, không crash
    try:
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            return io.TextIOWrapper(buf, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass
    return stream


def setup_logging(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "[%(asctime)s] %(name)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(_safe_console_stream())
    console.setFormatter(fmt)
    logger.addHandler(console)

    logger.propagate = True  # cho phép root logger (WebSocket handler) bắt được log
    return logger