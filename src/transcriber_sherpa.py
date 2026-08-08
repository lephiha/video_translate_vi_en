"""
src/transcriber_sherpa.py
Offline ASR dùng sherpa-onnx (SenseVoice) — fallback cho Groq Whisper.

Model khuyên dùng (tự động download lần đầu):
  sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
  → Hỗ trợ: Chinese, English, Japanese, Korean, Cantonese
  → File int8 (~150 MB), rất nhanh trên CPU
"""

import logging
import os
import subprocess
from src.ffmpeg_path import FFMPEG_PATH

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_DIR = os.path.join(
    os.environ.get(
        "SHERPA_MODELS_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models"),
    ),
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
)

_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
)

_LANG_MAP = {
    "en": "en", "en-US": "en",
    "zh": "zh", "zh-CN": "zh",
    "ja": "ja", "ja-JP": "ja",
    "ko": "ko", "ko-KR": "ko",
}


def download_model(model_dir: str = _DEFAULT_MODEL_DIR) -> str:
    model_onnx = os.path.join(model_dir, "model.int8.onnx")
    if os.path.exists(model_onnx):
        logger.info(f"[sherpa-ASR] Model đã có tại: {model_dir}")
        return model_dir

    parent = os.path.dirname(model_dir)
    os.makedirs(parent, exist_ok=True)

    archive = os.path.join(parent, "sense-voice.tar.bz2")
    logger.info(f"[sherpa-ASR] Downloading model từ {_MODEL_URL} ...")
    subprocess.run(["curl", "-L", "-o", archive, _MODEL_URL], check=True)

    logger.info("[sherpa-ASR] Giải nén model ...")
    subprocess.run(["tar", "xf", archive, "-C", parent], check=True)
    os.remove(archive)

    logger.info(f"[sherpa-ASR] Model sẵn sàng tại: {model_dir}")
    return model_dir


_recognizer_cache: dict = {}


def _get_recognizer(model_dir: str, lang: str, num_threads: int = 2):
    cache_key = (model_dir, lang, num_threads)
    if cache_key in _recognizer_cache:
        return _recognizer_cache[cache_key]

    try:
        import sherpa_onnx
    except ImportError:
        raise RuntimeError("sherpa-onnx chưa được cài. Chạy: pip install sherpa-onnx")

    model_file = os.path.join(model_dir, "model.int8.onnx")
    tokens_file = os.path.join(model_dir, "tokens.txt")

    if not os.path.exists(model_file):
        raise FileNotFoundError(
            f"Không tìm thấy model tại {model_file}. "
            "Chạy: python src/transcriber_sherpa.py --download-model"
        )

    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=model_file,
        tokens=tokens_file,
        num_threads=num_threads,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        debug=False,
        provider="cpu",
        language=lang,
        use_itn=True,
    )
    _recognizer_cache[cache_key] = recognizer
    logger.info(f"[sherpa-ASR] Recognizer khởi tạo thành công (lang={lang})")
    return recognizer


def _read_wav(wav_path: str):
    import wave as _wave
    import array

    with _wave.open(wav_path, "rb") as f:
        assert f.getnchannels() == 1, "Cần WAV mono"
        assert f.getsampwidth() == 2, "Cần WAV 16-bit"
        sample_rate = f.getframerate()
        num_frames = f.getnframes()
        raw = f.readframes(num_frames)

    samples_int16 = array.array("h", raw)
    samples_float = [s / 32768.0 for s in samples_int16]
    return samples_float, sample_rate


def transcribe_sherpa(
    audio_path: str,
    lang_code: str = "en",
    model_dir: str | None = None,
    num_threads: int = 2,
) -> list[dict]:
    if model_dir is None:
        model_dir = _DEFAULT_MODEL_DIR

    sherpa_lang = _LANG_MAP.get(lang_code, "auto")
    recognizer = _get_recognizer(model_dir, sherpa_lang, num_threads)

    wav_16k = audio_path
    if not audio_path.endswith("_16k.wav"):
        wav_16k = audio_path.replace(".wav", "_16k_tmp.wav")
        _resample_to_16k(audio_path, wav_16k)

    try:
        samples, sample_rate = _read_wav(wav_16k)
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        recognizer.decode_stream(stream)
        full_text = stream.result.text.strip()

        if not full_text:
            logger.warning("[sherpa-ASR] Không nhận dạng được nội dung nào")
            return []

        segments = _build_segments_from_text(full_text, samples, sample_rate)
        logger.info(f"[sherpa-ASR] Transcribed {len(segments)} segments")
        return segments

    finally:
        if wav_16k != audio_path and os.path.exists(wav_16k):
            os.remove(wav_16k)


def _resample_to_16k(src: str, dst: str) -> None:
    cmd = [
        FFMPEG_PATH, "-y", "-i", src,
        "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
        dst,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg resample failed: {result.stderr[-200:]}")


def _build_segments_from_text(full_text: str, samples: list, sample_rate: int) -> list[dict]:
    """SenseVoice không trả về word-level timestamps ở API cơ bản → chia câu,
    phân phối thời gian đều."""
    import re

    total_duration = len(samples) / sample_rate

    sentences = re.split(r"(?<=[.!?;])\s*", full_text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        sentences = [full_text]

    seg_duration = total_duration / len(sentences)
    segments = []
    for i, sent in enumerate(sentences):
        start = round(i * seg_duration, 3)
        end = round((i + 1) * seg_duration, 3)
        segments.append({
            "id": i,
            "text": sent,
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
        })

    return segments


def transcribe_with_sherpa_fallback(
    audio_path: str,
    lang_code: str,
    groq_fn,
    model_dir: str | None = None,
) -> list[dict]:
    """Thử Groq trước, nếu lỗi thì fallback sang sherpa-onnx offline."""
    try:
        logger.info("[ASR] Thử Groq Whisper ...")
        result = groq_fn(audio_path, lang_code)
        if result:
            return result
        logger.warning("[ASR] Groq trả về rỗng, fallback sherpa-onnx")
    except Exception as e:
        logger.warning(f"[ASR] Groq thất bại ({e}), fallback sherpa-onnx offline ...")

    return transcribe_sherpa(audio_path, lang_code, model_dir=model_dir)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-model", action="store_true")
    parser.add_argument("--test", metavar="WAV")
    parser.add_argument("--lang", default="en")
    args = parser.parse_args()

    if args.download_model:
        download_model()
        print("Model đã sẵn sàng.")

    if args.test:
        segs = transcribe_sherpa(args.test, args.lang)
        for s in segs:
            print(f"[{s['start']:.1f}s → {s['end']:.1f}s] {s['text']}")
