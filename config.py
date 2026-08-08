import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _get_app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _resolve_output_dir() -> str:
    raw = os.getenv("OUTPUT_DIR", "./output")
    if os.path.isabs(raw):
        return raw
    return os.path.join(_get_app_root(), raw.lstrip("./\\"))


# --- Translation providers (fallback chain: OpenAI -> Gemini -> Groq -> OpenRouter -> NLLB offline) ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")

# --- ASR ---
DEFAULT_SOURCE_LANG = os.getenv("DEFAULT_SOURCE_LANG", "en-US")
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))

# --- Offline translation (NLLB) ---
# distilled-600M (nhanh, nhẹ) | distilled-1.3B (mặc định, cân bằng) | 1.3B | 3.3B (nặng nhất)
NLLB_MODEL_NAME = os.getenv("NLLB_MODEL_NAME", "facebook/nllb-200-distilled-1.3B")

# --- Output ---
OUTPUT_DIR = _resolve_output_dir()

# --- Audio timing ---
# < 1.0 làm giọng đọc chậm lại một chút trước khi ghép, giúp khớp thời lượng
# gốc tốt hơn (đặt trong .env, mặc định không đổi tốc độ).
AUDIO_SLOW_FACTOR = float(os.getenv("AUDIO_SLOW_FACTOR", "1.0"))

# --- VieNeu TTS ---
VIENEU_MODE = os.getenv("VIENEU_MODE", "v3turbo")
VIENEU_VOICE_MALE = os.getenv("VIENEU_VOICE_MALE", "manhdung_ref")
VIENEU_VOICE_FEMALE = os.getenv("VIENEU_VOICE_FEMALE", "ngochuyen_ref")
VIENEU_FIT_TIMING = os.getenv("VIENEU_FIT_TIMING", "0")
VIENEU_CUSTOM_VOICES = os.getenv("VIENEU_CUSTOM_VOICES", "")
