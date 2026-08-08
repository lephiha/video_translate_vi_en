"""Xác định đường dẫn thực thi ffmpeg/ffprobe.

Ưu tiên biến môi trường FFMPEG_PATH/FFPROBE_PATH (cho phép người dùng trỏ tay),
sau đó thử binary bundle qua imageio-ffmpeg (không cần cài ffmpeg hệ thống),
cuối cùng fallback về "ffmpeg"/"ffprobe" trong PATH.
"""
import os
import shutil


def _resolve(env_name: str, exe_name: str) -> str:
    env_val = os.getenv(env_name, "")
    if env_val and os.path.exists(env_val):
        return env_val

    if exe_name == "ffmpeg":
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass

    found = shutil.which(exe_name)
    if found:
        return found

    return exe_name  # để subprocess tự báo lỗi rõ ràng nếu không có


FFMPEG_PATH = _resolve("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = _resolve("FFPROBE_PATH", "ffprobe")
