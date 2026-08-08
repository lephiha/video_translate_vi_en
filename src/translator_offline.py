"""Offline translation using HuggingFace transformers (NLLB or M2M100).
No API key required. First run downloads model weights.

NLLB model size/quality trade-off (đổi qua .env NLLB_MODEL_NAME):
  facebook/nllb-200-distilled-600M   ~2.4GB   nhanh nhất, chất lượng thấp nhất
  facebook/nllb-200-distilled-1.3B   ~5.2GB   mặc định — cân bằng tốt cho en-vi
  facebook/nllb-200-1.3B             ~5.2GB   bản không distill, đôi khi mượt hơn
  facebook/nllb-200-3.3B             ~13GB    chất lượng cao nhất, cần GPU/RAM lớn
"""
import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

DEFAULT_NLLB_MODEL = "facebook/nllb-200-distilled-1.3B"

NLLB_LANG_MAP = {
    "en": "eng_Latn", "en-US": "eng_Latn",
    "zh": "zho_Hans", "zh-CN": "zho_Hans",
    "ja": "jpn_Jpan", "ja-JP": "jpn_Jpan",
}

M2M_LANG_MAP = {
    "en": "en", "en-US": "en",
    "zh": "zh", "zh-CN": "zh",
    "ja": "ja", "ja-JP": "ja",
}


def translate_offline(
    segments: list[dict],
    source_lang: str,
    model_type: Literal["nllb", "m2m100"] = "nllb",
) -> list[dict]:
    """Translate segments offline. Adds 'text_vi' to each segment."""

    logger.info(f"Loading offline translation model: {model_type}")

    _translate_fn = _translate_nllb if model_type == "nllb" else _translate_m2m100

    texts = [s["text"] for s in segments]
    translated = _translate_fn(texts, source_lang)

    result = []
    for seg, vi_text in zip(segments, translated):
        result.append({**seg, "text_vi": vi_text})

    logger.info(f"Offline translation done: {len(result)} segments")
    return result


def _translate_nllb(texts: list[str], source_lang: str) -> list[str]:
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    import torch

    src = NLLB_LANG_MAP.get(source_lang, "eng_Latn")
    tgt = "vie_Latn"
    model_name = os.getenv("NLLB_MODEL_NAME", DEFAULT_NLLB_MODEL)

    logger.info(f"NLLB ({model_name}): {src} → {tgt}, loading model...")
    tokenizer = AutoTokenizer.from_pretrained(model_name, src_lang=src)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.eval()

    tgt_lang_id = tokenizer.convert_tokens_to_ids(tgt)

    results = []
    for i, text in enumerate(texts):
        if not text.strip():
            results.append("")
            continue
        inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            generated = model.generate(
                **inputs,
                forced_bos_token_id=tgt_lang_id,
                max_length=512,
            )
        out = tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
        results.append(out)
        if (i + 1) % 10 == 0:
            logger.info(f"  Translated {i+1}/{len(texts)} segments")

    return results


def _translate_m2m100(texts: list[str], source_lang: str) -> list[str]:
    from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

    src = M2M_LANG_MAP.get(source_lang, "en")

    logger.info("M2M100: loading model (first run may download ~2GB)...")
    tokenizer = M2M100Tokenizer.from_pretrained("facebook/m2m100_418M")
    model = M2M100ForConditionalGeneration.from_pretrained("facebook/m2m100_418M")
    tokenizer.src_lang = src

    results = []
    for i, text in enumerate(texts):
        if not text.strip():
            results.append("")
            continue
        encoded = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        generated = model.generate(
            **encoded,
            forced_bos_token_id=tokenizer.get_lang_id("vi"),
        )
        out = tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
        results.append(out)
        if (i + 1) % 10 == 0:
            logger.info(f"  Translated {i+1}/{len(texts)} segments")

    return results
