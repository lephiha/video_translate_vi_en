import os
import subprocess
import config
from src.ffmpeg_path import FFMPEG_PATH, FFPROBE_PATH
from src.utils import setup_logging

logger = setup_logging("audio_extractor")


def _has_audio_stream(video_path: str) -> bool:
    """Kiểm tra video có ít nhất 1 audio stream hay không, dùng ffprobe."""
    cmd = [
        FFPROBE_PATH, "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(f"ffprobe kiểm tra audio stream thất bại (coi như không có audio): {result.stderr[-300:]}")
        return False
    return bool(result.stdout.strip())


def extract_audio(video_path: str, output_path: str) -> str:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    if not _has_audio_stream(video_path):
        raise RuntimeError(
            f"Video không có audio track nào: {video_path}. "
            f"Kiểm tra lại video gốc có tiếng không trước khi chạy lại pipeline."
        )

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

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr}")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Audio extraction produced empty file: {output_path}")

    logger.info(f"Audio extracted: {output_path} ({os.path.getsize(output_path)} bytes)")
    return output_path
