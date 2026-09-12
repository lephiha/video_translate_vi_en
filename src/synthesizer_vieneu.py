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
import threading
from pathlib import Path

logger = logging.getLogger("synthesizer_vieneu")

_MODEL_DIRNAME = "models--pnnbao-ump--VieNeu-TTS-v3-Turbo"

_MODEL_REQUIRED_FILES = (
    'onnx_int8/config.json',
    'onnx_int8/tokenizer.json',
    'onnx_int8/vieneu_acoustic_cached.onnx',
    'onnx_int8/vieneu_backbone_shared.data',
    'onnx_int8/vieneu_decode_step.onnx',
    'onnx_int8/vieneu_prefill.onnx',
    'onnx_int8/vieneu_v3_heads.npz',
)

try:
    import config
except Exception:  # pragma: no cover
    config = None

MAX_TTS_RETRY = 3
_engine = None
_engine_lock = threading.Lock()
_loading_thread = None
_load_error = None


def _cfg(name: str, default=None):
    if config is not None and hasattr(config, name):
        val = getattr(config, name)
        if val not in (None, ""):
            return val
    return os.getenv(name, default)


def is_model_cache_ready(model_dir: str | os.PathLike[str]) -> bool:
    '''Return true only for a complete VieNeu Hugging Face snapshot.'''
    snapshots_dir = Path(model_dir) / 'snapshots'
    if not snapshots_dir.is_dir():
        return False

    for snapshot in snapshots_dir.iterdir():
        if not snapshot.is_dir():
            continue
        if all(
            (snapshot / relative_path).is_file()
            and (snapshot / relative_path).stat().st_size > 0
            for relative_path in _MODEL_REQUIRED_FILES
        ):
            return True
    return False


def _ensure_hf_cache():
    """Trỏ cache Hugging Face về APP_DATA_DIR để model VieNeu tải 1 lần và
    dùng offline mãi. PHẢI gọi TRƯỚC khi import vieneu."""
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
    app_dir = os.environ.get("APP_DATA_DIR")
    if not app_dir:
        return
    cache = os.path.join(app_dir, "vieneu_models")
    os.makedirs(cache, exist_ok=True)
    os.environ.setdefault("HF_HOME", cache)
    os.environ.setdefault("HF_HUB_CACHE", os.path.join(cache, "hub"))

    model_dir = os.path.join(cache, "hub", _MODEL_DIRNAME)
    if is_model_cache_ready(model_dir):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def get_engine(wait_timeout: float = 30.0):
    """Trả về Vieneu engine, nạp 1 lần rồi cache.

    Việc load model chạy trong 1 thread nền RIÊNG, chỉ khởi động DUY NHẤT
    LẦN ĐẦU (singleton qua _loading_thread). Mỗi lần get_engine() được gọi,
    nó chỉ ĐỢI tối đa wait_timeout giây rồi thôi — không còn treo vô hạn
    dù thread nền tải chậm/treo bao lâu. Nếu chưa xong, raise TimeoutError
    rõ ràng thay vì để cả server đứng hình theo.
    """
    global _engine, _loading_thread, _load_error
    if _engine is not None:
        return _engine

    with _engine_lock:
        if _engine is not None:
            return _engine
        if _loading_thread is not None and not _loading_thread.is_alive():
            # Retry a failed or interrupted first download in the same process.
            _loading_thread = None
            _load_error = None
        if _loading_thread is None:
            def _load():
                global _engine, _load_error
                try:
                    _ensure_hf_cache()
                    from vieneu import Vieneu
                    mode = str(_cfg("VIENEU_MODE", "v3turbo") or "v3turbo").strip()
                    logger.info(f"Khởi tạo VieNeu engine (mode={mode}) — lần đầu sẽ tải model...")
                    eng = Vieneu(mode=mode) if (mode and mode != "v3turbo") else Vieneu()
                    _engine = eng
                    logger.info("VieNeu engine sẵn sàng.")
                except Exception as e:
                    _load_error = e
                    logger.error(f"Load VieNeu thất bại: {e}", exc_info=True)

            _loading_thread = threading.Thread(target=_load, daemon=True)
            _loading_thread.start()

    _loading_thread.join(timeout=wait_timeout)

    if _engine is not None:
        return _engine
    if _load_error is not None:
        raise RuntimeError(f"VieNeu load lỗi: {_load_error}")
    raise TimeoutError(
        f"VieNeu vẫn đang tải model (>{wait_timeout}s) — model vẫn tải "
        f"ngầm phía sau, thử lại sau ít phút."
    )


def prewarm_vieneu():
    """Gọi lúc server khởi động để tải model sẵn, tránh để người dùng chờ
    lúc lồng tiếng đầu tiên. Trả về True nếu sẵn sàng."""
    try:
        get_engine(wait_timeout=170)
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
    """Đọc voice khai trong env và tự tìm mọi file WAV trong thư mục voices."""
    raw = _cfg("VIENEU_CUSTOM_VOICES", "") or ""
    result = []
    seen_keys = set()
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
            seen_keys.add(key.casefold())
        else:
            logger.warning(f"Giọng custom '{key}' không tìm thấy file: {ref_path}")

    for wav_path in _discover_voice_files():
        key = os.path.splitext(os.path.basename(wav_path))[0]
        if key.casefold() in seen_keys:
            continue
        result.append((key, _infer_custom_gender(key), wav_path))
        seen_keys.add(key.casefold())
    return result


def _voice_directories() -> list[str]:
    """Các thư mục voice có thể ghi bởi người dùng hoặc bundle bởi PyInstaller."""
    candidates = []
    app_dir = os.environ.get("APP_DATA_DIR")
    if app_dir:
        candidates.append(os.path.join(app_dir, "voices"))

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "voices"))

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.extend([
            os.path.join(exe_dir, "voices"),
            os.path.join(exe_dir, "_internal", "voices"),
        ])

    source_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
    candidates.append(os.path.join(source_root, "voices"))
    return list(dict.fromkeys(os.path.normpath(path) for path in candidates))


def _discover_voice_files() -> list[str]:
    found = []
    seen_paths = set()
    for directory in _voice_directories():
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith(".wav"):
                continue
            path = os.path.join(directory, name)
            normalized = os.path.normcase(os.path.abspath(path))
            if normalized not in seen_paths:
                found.append(path)
                seen_paths.add(normalized)
    return found


def _infer_custom_gender(key: str) -> str:
    normalized = key.casefold().replace("-", "_")
    parts = normalized.split("_")
    if any(part in ("female", "nu") for part in parts):
        return "female"
    if any(part in ("male", "nam") for part in parts):
        return "male"
    return "unknown"


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
    try:
        preset_voices = list_voices()
    except Exception as exc:
        # Voice clone là file local, không nên biến mất chỉ vì model/preset chưa
        # tải được trên máy mới hoặc Hugging Face tạm thời mất kết nối.
        logger.warning(f"Không tải được danh sách giọng preset: {exc}")
        preset_voices = []

    for label, vid in preset_voices:
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
