import json
import math
import os
import shutil
import subprocess
import tempfile
import re

from groq import Groq
from src.utils import setup_logging
from src.ffmpeg_path import FFMPEG_PATH, FFPROBE_PATH

logger = setup_logging("transcriber_groq")

GROQ_LANG_MAP = {
    "en-US": "en", "en-GB": "en", "en": "en",
    "vi-VN": "vi",
}

GROQ_MAX_MB = 24  # Groq limit là 25MB, để an toàn dùng 24


def _get_groq_keys() -> list[str]:
    keys = [
        os.getenv("GROQ_API_KEY", ""),
        os.getenv("GROQ_API_KEY_2", ""),
        os.getenv("GROQ_API_KEY_3", ""),
    ]
    return [k.strip() for k in keys if k.strip()]


def _get_file_mb(path: str) -> float:
    return os.path.getsize(path) / (1024 * 1024)


def _get_duration(audio_path: str) -> float:
    try:
        out = subprocess.check_output([
            FFPROBE_PATH, "-v", "quiet",
            "-print_format", "json",
            "-show_format", audio_path,
        ], text=True)
        return float(json.loads(out)["format"]["duration"])
    except Exception:
        return 0.0


def _chunk_audio(audio_path: str, chunk_minutes: int = 10) -> list[tuple[str, float]]:
    total_seconds = _get_duration(audio_path)
    chunk_seconds = chunk_minutes * 60
    n_chunks = math.ceil(total_seconds / chunk_seconds) if total_seconds > 0 else 1

    if n_chunks <= 1:
        return [(audio_path, 0.0)]

    logger.info(f"Splitting audio into {n_chunks} chunks ({chunk_minutes} min each)...")
    tmp_dir = tempfile.mkdtemp(prefix="groq_chunks_")
    chunks = []

    for i in range(n_chunks):
        start = i * chunk_seconds
        out_path = os.path.join(tmp_dir, f"chunk_{i:03d}.wav")
        subprocess.run([
            FFMPEG_PATH, "-y", "-i", audio_path,
            "-ss", str(start),
            "-t", str(chunk_seconds),
            "-ar", "16000", "-ac", "1",
            out_path,
        ], capture_output=True)

        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            size_mb = _get_file_mb(out_path)
            logger.info(f"  Chunk {i+1}/{n_chunks}: offset={start:.0f}s, size={size_mb:.1f}MB")
            chunks.append((out_path, float(start)))
        else:
            logger.warning(f"  Chunk {i+1} empty, skipping")

    return chunks


def _transcribe_chunk(keys: list[str], chunk_path: str, lang: str, time_offset: float = 0.0) -> list[dict]:
    last_err = None

    for key in keys:
        try:
            client = Groq(api_key=key)
            with open(chunk_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk_path), f),
                    model="whisper-large-v3",
                    language=lang,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )

            segments = []
            for seg in (response.segments or []):
                text = seg.get("text", "").strip()
                if not text:
                    continue
                start = round(float(seg.get("start", 0)) + time_offset, 3)
                end = round(float(seg.get("end", 0)) + time_offset, 3)
                segments.append({
                    "text": text,
                    "start": start,
                    "end": end,
                    "duration": round(end - start, 3),
                })

            logger.info(f"  Key ...{key[-6:]}: OK → {len(segments)} segments")
            return segments

        except Exception as e:
            logger.warning(f"  Key ...{key[-6:]} failed: {e}")
            last_err = e
            continue

    raise RuntimeError(f"Tất cả Groq keys thất bại: {last_err}")


def transcribe(audio_path: str, language: str) -> list[dict]:
    keys = _get_groq_keys()
    if not keys:
        raise RuntimeError("Không có GROQ_API_KEY nào trong .env")

    lang = GROQ_LANG_MAP.get(language, language.split("-")[0])
    file_mb = _get_file_mb(audio_path)
    logger.info(f"Transcribing: {audio_path} ({file_mb:.1f}MB, lang={lang}, keys={len(keys)})")

    if file_mb > GROQ_MAX_MB:
        logger.info(f"File {file_mb:.1f}MB > {GROQ_MAX_MB}MB → chunking...")
        chunks = _chunk_audio(audio_path, chunk_minutes=10)
        is_chunked = len(chunks) > 1
    else:
        chunks = [(audio_path, 0.0)]
        is_chunked = False

    all_raw = []
    tmp_dir = None

    try:
        for idx, (chunk_path, time_offset) in enumerate(chunks):
            if is_chunked:
                logger.info(f"Transcribing chunk {idx+1}/{len(chunks)} (offset={time_offset:.0f}s)...")
                tmp_dir = os.path.dirname(chunk_path)

            segs = _transcribe_chunk(keys, chunk_path, lang, time_offset)
            all_raw.extend(segs)

    finally:
        if is_chunked and tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.info("Temp chunks cleaned up")

    segments = []
    for i, seg in enumerate(all_raw, 1):
        segments.append({**seg, "id": i})
        logger.info(f"Segment {i}: [{seg['start']:.1f}s-{seg['end']:.1f}s] {seg['text'][:50]}")

    logger.info(f"Transcription complete: {len(segments)} segments")
    segments = split_long_segments(segments)
    return segments


def split_long_segments(segments: list[dict], max_duration: float = 10.0) -> list[dict]:
    result = []
    new_id = 0
    for seg in segments:
        if seg["duration"] <= max_duration:
            new_id += 1
            result.append({**seg, "id": new_id})
            continue
        sentences = re.split(r'(?<=[.!?;])\s+', seg["text"].strip())
        if len(sentences) <= 1:
            new_id += 1
            result.append({**seg, "id": new_id})
            continue
        total_chars = sum(len(s) for s in sentences)
        start = seg["start"]
        chunk_sentences, chunk_chars = [], 0
        for sentence in sentences:
            est = (chunk_chars + len(sentence)) / total_chars * seg["duration"]
            if chunk_sentences and est > max_duration:
                dur = chunk_chars / total_chars * seg["duration"]
                end = round(start + dur, 3)
                new_id += 1
                result.append({
                    "id": new_id,
                    "text": " ".join(chunk_sentences),
                    "start": round(start, 3),
                    "end": end,
                    "duration": round(dur, 3),
                })
                start = end
                chunk_sentences, chunk_chars = [], 0
            chunk_sentences.append(sentence)
            chunk_chars += len(sentence)
        if chunk_sentences:
            new_id += 1
            result.append({
                "id": new_id,
                "text": " ".join(chunk_sentences),
                "start": round(start, 3),
                "end": seg["end"],
                "duration": round(seg["end"] - start, 3),
            })
    return result


def save_transcript(segments: list[dict], output_path: str) -> str:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    logger.info(f"Transcript saved: {output_path}")
    return output_path
