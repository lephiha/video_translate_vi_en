"""
src/synthesizer_vieneu.py — Vietnamese TTS backend dùng VieNeu-TTS (on-device).

Interface:
    synthesize_segment_vieneu(text_vi, output_path, target_duration=None,
                              voice=None, gender=None)
        -> {"path", "actual_duration", "speed_adjusted", "rate_applied"}

GHI CHÚ TRIỂN KHAI:
- Model tải tự động từ Hugging Face LẦN ĐẦU `Vieneu()` được khởi tạo. Để model
  nằm ở thư mục bền (không bị xoá khi tắt app), file này set HF cache về
  APP_DATA_DIR TRƯỚC khi import vieneu.
- Engine nặng → nạp MỘT LẦN rồi cache (singleton).
- KHÔNG ép tốc độ (speed-hack) mặc định — co/giãn audio bằng ffmpeg atempo
  gây méo/robot. Có cờ VIENEU_FIT_TIMING để bật atempo NHẸ nếu thật sự cần.
"""
import logging
import os
import sys

logger = logging.getLogger("synthesizer_vieneu")

try:
    import config
except Exception:  # pragma: no cover
    config = None

MAX_TTS_RETRY = 3
_engine = None


def _cfg(name: str, default=None):
    if config is not None and hasattr(config, name):
        val = getattr(config, name)
        if val not in (None, ""):
            return val
    return os.getenv(name, default)


def _ensure_hf_cache():
    """Trỏ cache Hugging Face về APP_DATA_DIR để model VieNeu tải 1 lần và
    dùng offline mãi. PHẢI gọi TRƯỚC khi import vieneu."""
    app_dir = os.environ.get("APP_DATA_DIR")
    if not app_dir:
        return
    cache = os.path.join(app_dir, "vieneu_models")
    os.makedirs(cache, exist_ok=True)
    os.environ.setdefault("HF_HOME", cache)
    os.environ.setdefault("HF_HUB_CACHE", os.path.join(cache, "hub"))


def get_engine():
    """Trả về Vieneu engine, nạp 1 lần rồi cache. Lần đầu sẽ tải model từ HF."""
    global _engine
    if _engine is not None:
        return _engine

    _ensure_hf_cache()
    from vieneu import Vieneu

    mode = str(_cfg("VIENEU_MODE", "v3turbo") or "v3turbo").strip()
    logger.info(f"Khởi tạo VieNeu engine (mode={mode}) — lần đầu sẽ tải model...")
    if mode and mode != "v3turbo":
        _engine = Vieneu(mode=mode)
    else:
        _engine = Vieneu()
    logger.info("VieNeu engine sẵn sàng.")
    return _engine


def prewarm_vieneu():
    """Gọi lúc server khởi động để tải model sẵn, tránh để người dùng chờ
    lúc lồng tiếng đầu tiên. Trả về True nếu sẵn sàng."""
    try:
        get_engine()
        return True
    except Exception as e:
        logger.error(f"Prewarm VieNeu thất bại: {e}", exc_info=True)
        return False


def list_voices():
    """Liệt kê các giọng preset gốc của VieNeu: list các tuple (label, voice_id)."""
    return list(get_engine().list_preset_voices())


def _resolve_ref_audio_path(rel_or_abs: str) -> str:
    if not rel_or_abs:
        return rel_or_abs
    if os.path.isabs(rel_or_abs):
        return rel_or_abs

    candidates = []

    app_dir = os.environ.get("APP_DATA_DIR")
    if app_dir:
        candidates.append(os.path.join(app_dir, rel_or_abs))

    # App đã đóng gói (PyInstaller) — data được giải nén vào _MEIPASS
    # (onedir mode: chính là thư mục _internal cạnh file exe).
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, rel_or_abs))

    # Cùng thư mục chứa file .exe (phòng trường hợp datas đặt sai gốc).
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, rel_or_abs))
        candidates.append(os.path.join(exe_dir, "_internal", rel_or_abs))

    # Chạy dev (chưa đóng gói) — dựa theo vị trí file source.
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.normpath(os.path.join(here, "..", rel_or_abs)))

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    logger.warning(f"Không tìm thấy ref_audio ở bất kỳ candidate nào: {candidates}")
    return candidates[-1]


def list_custom_voices() -> list[tuple[str, str, str]]:
    """Đọc VIENEU_CUSTOM_VOICES — format 'key:gender:path,key2:gender2:path2'."""
    raw = _cfg("VIENEU_CUSTOM_VOICES", "") or ""
    result = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        parts = entry.split(":", 2)
        if len(parts) == 3:
            key, gender, path = parts
            gender = gender.strip().lower()
            if gender not in ("male", "female"):
                logger.warning(f"Giọng custom '{key}': gender '{gender}' không hợp lệ, dùng 'unknown'.")
                gender = "unknown"
        else:
            key, path = parts
            gender = "unknown"
            logger.warning(f"Giọng custom '{key}': thiếu gender (format cũ) — sửa thành 'key:male|female:path'.")
        key = key.strip()
        ref_path = _resolve_ref_audio_path(path.strip())
        if os.path.exists(ref_path):
            result.append((key, gender, ref_path))
        else:
            logger.warning(f"Giọng custom '{key}' không tìm thấy file: {ref_path}")
    return result


def _find_custom_path(name: str) -> str | None:
    if not name:
        return None
    for key, _gender, path in list_custom_voices():
        if key == name:
            return path
    return None


def _infer_preset_gender(label: str) -> str:
    low = label.lower()
    if "nữ" in low:
        return "female"
    if "nam" in low:
        return "male"
    return "unknown"


def list_all_voices() -> list[dict]:
    """Danh sách đầy đủ cho UI: preset + custom, kèm sample_id để phát thử
    và gender để nhóm dropdown Nam/Nữ."""
    male_default = _cfg("VIENEU_VOICE_MALE", "manhdung_ref")
    female_default = _cfg("VIENEU_VOICE_FEMALE", "ngochuyen_ref")

    out = []
    for label, vid in list_voices():
        out.append({
            "label": label, "voice_id": vid, "kind": "preset",
            "sample_id": f"preset__{vid}",
            "gender": _infer_preset_gender(label),
            "is_default": vid in (male_default, female_default),
        })
    for key, gender, _path in list_custom_voices():
        out.append({
            "label": f"{key} (tuỳ chỉnh)", "voice_id": key, "kind": "custom",
            "sample_id": f"custom__{key}",
            "gender": gender,
            "is_default": key in (male_default, female_default),
        })
    return out


def _resolve_voice(voice, gender):
    if voice:
        return voice
    g = (gender or "").strip().lower()
    if g.startswith(("f", "n", "nu", "nữ", "female")):
        return _cfg("VIENEU_VOICE_FEMALE", "ngochuyen_ref")
    return _cfg("VIENEU_VOICE_MALE", "manhdung_ref")


def _infer_with_voice(eng, text: str, voice_name: str | None, ref_audio: str | None):
    if ref_audio:
        return eng.infer(text, ref_audio=ref_audio)
    custom_path = _find_custom_path(voice_name)
    if custom_path:
        return eng.infer(text, ref_audio=custom_path)
    return eng.infer(text, voice=voice_name) if voice_name else eng.infer(text)


SAMPLE_TEXT = "Xin chào, đây là giọng đọc mẫu."
_SAMPLES_DIR_NAME = "voice_samples"


def _samples_dir() -> str:
    app_dir = os.environ.get("APP_DATA_DIR")
    base = app_dir if app_dir else os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, _SAMPLES_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def ensure_voice_sample(sample_id: str) -> str | None:
    """Tạo (nếu chưa có) file wav mẫu ngắn cho 1 giọng để nghe thử. Cache lại."""
    out_path = os.path.join(_samples_dir(), f"{sample_id.replace('/', '_')}.wav")
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path

    kind, _, key = sample_id.partition("__")
    try:
        if kind in ("preset", "custom"):
            synthesize_segment_vieneu(SAMPLE_TEXT, out_path, voice=key)
        else:
            return None
        return out_path
    except Exception as e:
        logger.error(f"Tạo voice sample thất bại ({sample_id}): {e}")
        return None


def synthesize_segment_vieneu(
    text_vi: str,
    output_path: str,
    target_duration: float | None = None,
    voice: str | None = None,
    gender: str | None = None,
    ref_audio: str | None = None,
) -> dict:
    """Sinh giọng tiếng Việt từ text bằng VieNeu, ghi ra output_path (wav)."""
    text = (text_vi or "").strip()
    if not text:
        raise ValueError("text_vi rỗng.")

    if len(text.split()) < 5 and not text.endswith((".", "!", "?", "…")):
        text = text + " ."

    chosen_voice = _resolve_voice(voice, gender)

    eng = get_engine()
    last_err = None
    for attempt in range(1, MAX_TTS_RETRY + 1):
        try:
            audio = _infer_with_voice(eng, text, chosen_voice, ref_audio)
            eng.save(audio, output_path)
            last_err = None
            break
        except Exception as e:
            last_err = e
            logger.warning(
                f"VieNeu infer lần {attempt}/{MAX_TTS_RETRY} lỗi (voice={chosen_voice}): {e}"
            )
    if last_err is not None:
        raise RuntimeError(f"VieNeu TTS thất bại sau {MAX_TTS_RETRY} lần: {last_err}")

    from pydub import AudioSegment
    seg = AudioSegment.from_file(output_path)

    speed_adjusted = False
    rate_applied = "1.0x"
    actual = len(seg) / 1000.0
    fit = str(_cfg("VIENEU_FIT_TIMING", "0")).strip().lower() in ("1", "true", "yes")
    if fit and target_duration and actual > target_duration * 1.1:
        ratio = min(actual / target_duration, 1.15)
        if ratio > 1.0:
            try:
                seg = seg.speedup(playback_speed=ratio)
                speed_adjusted = True
                rate_applied = f"{round(ratio, 2)}x"
            except Exception as e:
                logger.warning(f"Căn timing thất bại (bỏ qua, dùng audio gốc): {e}")

    seg.export(output_path, format="wav")
    actual = len(seg) / 1000.0

    if target_duration:
        logger.info(
            f"VieNeu: {len(text_vi)} chars, voice={chosen_voice}, "
            f"actual={actual:.1f}s, target={target_duration:.1f}s, rate={rate_applied}"
        )

    return {
        "path": output_path,
        "actual_duration": round(actual, 3),
        "speed_adjusted": speed_adjusted,
        "rate_applied": rate_applied,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Các giọng preset:")
    for label, vid in list_voices():
        print(" -", label, "→", vid)
    out = synthesize_segment_vieneu(
        "Xin chào, đây là giọng VieNeu đang đọc thử.",
        "vieneu_test.wav",
        gender="male",
    )
    print("Kết quả:", out)
