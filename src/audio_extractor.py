import os
import subprocess
import config
from src.ffmpeg_path import FFMPEG_PATH
from src.utils import setup_logging

logger = setup_logging("audio_extractor")


def extract_audio(video_path: str, output_path: str) -> str:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    sample_rate = str(config.AUDIO_SAMPLE_RATE)

    cmd = [
        FFMPEG_PATH, "-i", video_path,
        "-vn",
        "-ar", sample_rate,
        "-ac", "1",
        "-acodec", "pcm_s16le",
        "-y",
        output_path,
    ]

    logger.info(f"Extracting audio: {video_path} → {output_path}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError(
            f"FFmpeg không chạy được ({FFMPEG_PATH}). Bản cài đặt có thể thiếu FFmpeg: {exc}"
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr or "Không có thông tin lỗi."
        if "does not contain any stream" in stderr or "matches no streams" in stderr:
            raise RuntimeError(f"Video không có audio track nào: {video_path}.")
        raise RuntimeError(f"FFmpeg trích xuất audio thất bại: {stderr}")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Audio extraction produced empty file: {output_path}")

    logger.info(f"Audio extracted: {output_path} ({os.path.getsize(output_path)} bytes)")
    return output_path
