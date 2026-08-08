"""English → Vietnamese Video Dubbing Pipeline.

Nhận 1 file video local (đã upload), lồng tiếng Việt, trả về report dict.
Không có bước tải video, không đăng tải mạng xã hội, không sinh metadata.
"""
import json
import os
import subprocess
import time
from datetime import datetime

import config
from src.utils import setup_logging, ensure_dir
from src.audio_extractor import extract_audio
from src.vocal_separator import separate_vocals
from src.video_merger import merge_video
from src.audio_merger import merge_segments, fit_segments_to_timeline
from src.srt_generator import generate_srt
from src.ffmpeg_path import FFMPEG_PATH

logger = setup_logging("pipeline")

LANG_MAP = {"en": "en-US", "en-US": "en-US"}


def _translate_segments(segments: list[dict], source_lang: str) -> list[dict]:
    """Dịch segments. Fallback: OpenAI → Gemini → Groq → OpenRouter → NLLB offline."""
    prompt_template = lambda segs: f"""You are translating an ASR transcript for a dubbed video from {source_lang} to Vietnamese.
Below is a JSON array of segments. Each has: id, text, start, end, duration (seconds).

OUTPUT FORMAT (STRICT):
- Return ONE JSON array, same length, same order, same ids.
- Preserve every original field: id, text, start, end, duration.
- ADD one new string field per segment: "text_vi"
- Output valid JSON only — no markdown fences, no commentary.

STYLE:
- Bạn / mình / các bạn (never mày/tao).
- Natural, casual spoken tone, direct, skip filler words.
- Brand names stay original.

DURATION-AWARE LENGTH:
- Short (<4s): shortest natural phrasing.
- Medium (4-8s): natural casual speech.
- Long (>8s): avoid bloat.

Now translate this JSON, return only the translated JSON array:
{json.dumps(segs, ensure_ascii=False)}"""

    def _parse(text: str) -> list:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)

    def _chunk_translate(fn, segments, chunk=15):
        all_results = []
        for i in range(0, len(segments), chunk):
            all_results.extend(fn(segments[i:i + chunk]))
        return all_results

    MAX_RETRY = 3

    # --- 1. OpenAI ---
    openai_key = os.getenv("OPENAI_API_KEY", "")
    openai_model = os.getenv("OPENAI_MODEL", config.OPENAI_MODEL)
    if openai_key:
        for attempt in range(1, MAX_RETRY + 1):
            try:
                from openai import OpenAI
                client = OpenAI(api_key=openai_key)

                def _openai_translate(segs):
                    response = client.responses.create(model=openai_model, input=prompt_template(segs))
                    if not response.output_text:
                        raise RuntimeError("OpenAI trả về nội dung rỗng")
                    return _parse(response.output_text)

                result = _chunk_translate(_openai_translate, segments)
                logger.info(f"Translation done via OpenAI ({openai_model})")
                return result
            except Exception as e:
                logger.warning(f"OpenAI attempt {attempt}/{MAX_RETRY} failed: {e}")
                if attempt < MAX_RETRY:
                    time.sleep(2 * attempt)

    # --- 2. Gemini ---
    for key_env in ["GOOGLE_API_KEY", "GOOGLE_API_KEY_2"]:
        g_key = os.getenv(key_env, "")
        if not g_key:
            continue
        key_label = "Gemini key1" if key_env == "GOOGLE_API_KEY" else "Gemini key2"
        for attempt in range(1, MAX_RETRY + 1):
            try:
                import google.generativeai as genai
                genai.configure(api_key=g_key)
                model = genai.GenerativeModel("gemini-2.0-flash")

                def _gemini_translate(segs):
                    resp = model.generate_content(prompt_template(segs))
                    return _parse(resp.text)

                result = _chunk_translate(_gemini_translate, segments)
                logger.info(f"Translation done via {key_label}")
                return result
            except Exception as e:
                logger.warning(f"{key_label} attempt {attempt}/{MAX_RETRY} failed: {e}")
                if attempt < MAX_RETRY:
                    time.sleep(2 * attempt)

    # --- 3. Groq ---
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        for attempt in range(1, MAX_RETRY + 1):
            try:
                from groq import Groq
                client = Groq(api_key=groq_key)

                def _groq_translate(segs):
                    resp = client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[{"role": "user", "content": prompt_template(segs)}],
                        temperature=0.3, max_tokens=4096,
                    )
                    return _parse(resp.choices[0].message.content)

                result = _chunk_translate(_groq_translate, segments)
                logger.info("Translation done via Groq")
                return result
            except Exception as e:
                logger.warning(f"Groq attempt {attempt}/{MAX_RETRY} failed: {e}")
                if attempt < MAX_RETRY:
                    time.sleep(2 * attempt)

    # --- 4. OpenRouter ---
    or_key = os.getenv("OPENROUTER_API_KEY", "")
    or_model = os.getenv("OPENROUTER_MODEL", config.OPENROUTER_MODEL)
    if or_key:
        for attempt in range(1, MAX_RETRY + 1):
            try:
                import requests as _req

                def _or_translate(segs):
                    resp = _req.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"},
                        json={"model": or_model, "messages": [{"role": "user", "content": prompt_template(segs)}], "temperature": 0.3},
                        timeout=120,
                    )
                    resp.raise_for_status()
                    return _parse(resp.json()["choices"][0]["message"]["content"])

                result = _chunk_translate(_or_translate, segments)
                logger.info(f"Translation done via OpenRouter ({or_model})")
                return result
            except Exception as e:
                logger.warning(f"OpenRouter attempt {attempt}/{MAX_RETRY} failed: {e}")
                if attempt < MAX_RETRY:
                    time.sleep(2 * attempt)

    logger.warning("Tất cả API dịch thất bại, fallback NLLB offline...")
    from src.translator_offline import translate_offline
    result = translate_offline(segments, source_lang, model_type="nllb")
    logger.info("Translation done via NLLB offline (fallback)")
    return result


PRESET_STYLES = {
    "tiktok": {"FontName": "Arial", "FontSize": 20, "Bold": 1, "PrimaryColour": "&H00FFFFFF",
               "OutlineColour": "&H00000000", "Outline": 6, "Shadow": 0, "BorderStyle": 1,
               "Alignment": 2, "MarginV": 80},
    "news": {"FontName": "Arial", "FontSize": 14, "Bold": 0, "PrimaryColour": "&HFFFFFF",
             "BackColour": "&H80000000", "BorderStyle": 4, "Alignment": 2},
    "clean": {"FontName": "Arial", "FontSize": 15, "Bold": 0, "PrimaryColour": "&HFFFFFF",
              "Outline": 1, "Shadow": 0, "Alignment": 2},
}


def _burn_subtitle(video_path: str, segments: list[dict], style: str, work_dir: str) -> str:
    srt_path = os.path.join(work_dir, "transcript_vi.srt")
    generate_srt(segments, srt_path, text_field="text_vi")

    out_path = video_path.replace(".mp4", "_subbed.mp4")
    base = dict(PRESET_STYLES.get(style, PRESET_STYLES["tiktok"]))
    force_style = ",".join(f"{k}={v}" for k, v in base.items())
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

    cmd = [
        FFMPEG_PATH, "-y", "-i", video_path,
        "-vf", f"subtitles='{srt_escaped}':force_style='{force_style}'",
        "-c:a", "copy", out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.warning(f"Burn subtitle failed: {proc.stderr[-300:]}")
        return video_path

    logger.info(f"Subtitles burned: {out_path}")
    return out_path


def run_pipeline(
    file_path: str,
    voice_id: str,
    output_dir: str,
    source_lang: str = "en",
    bg_mode: str = "demucs",
    bg_duck_db: float = -12.0,
    translate_mode: str = "api",
    offline_model: str = "nllb",
    burn_sub: bool = False,
    sub_style: str = "tiktok",
) -> dict:
    start_time = time.time()
    lang_code = LANG_MAP.get(source_lang, "en-US")
    logger.info(f"Source language: {lang_code} → Vietnamese")

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found: {file_path}")

    folder_name = datetime.now().strftime("%Y%m%d%H%M%S")
    work_dir = ensure_dir(os.path.join(output_dir, folder_name))
    logger.info(f"Output folder: {work_dir}")

    audio_path = os.path.join(work_dir, "original_audio.wav")

    # --- Step 1: Extract audio ---
    logger.info("=" * 60)
    logger.info("STEP 1: Extracting audio")
    extract_audio(file_path, audio_path)

    # --- Step 2: Background track ---
    background_path = None
    background_gain_db = 0.0
    if bg_mode == "demucs":
        logger.info("=" * 60)
        logger.info("STEP 2: Separating vocals from original audio (Demucs)")
        sep = separate_vocals(audio_path, work_dir)
        background_path = sep.get("no_vocals")
        if background_path is None:
            logger.warning("Vocal separation unavailable — dubbed audio will use a silent base")
    elif bg_mode == "duck":
        logger.info("=" * 60)
        logger.info(f"STEP 2: Ducking original audio by {bg_duck_db:+.1f} dB")
        background_path = audio_path
        background_gain_db = bg_duck_db
    elif bg_mode == "none":
        logger.info("STEP 2 skipped: bg_mode=none")

    # --- Step 3: ASR ---
    logger.info("=" * 60)
    logger.info("STEP 3: Transcribing audio (ASR)")
    from src.transcriber_sherpa import transcribe_with_sherpa_fallback
    from src.transcriber_groq import transcribe as _groq_transcribe, save_transcript

    segments = transcribe_with_sherpa_fallback(
        audio_path=audio_path, lang_code=lang_code,
        groq_fn=lambda p, l: _groq_transcribe(p, l),
    )
    save_transcript(segments, os.path.join(work_dir, "transcript_original.json"))
    generate_srt(segments, os.path.join(work_dir, "transcript_original.srt"), text_field="text")
    logger.info(f"Transcribed {len(segments)} segments")

    # --- Step 4: Translate ---
    logger.info("=" * 60)
    logger.info("STEP 4: Translating to Vietnamese")
    if translate_mode == "offline":
        from src.translator_offline import translate_offline
        segments = translate_offline(segments, source_lang, model_type=offline_model)
    else:
        segments = _translate_segments(segments, source_lang)
    save_transcript(segments, os.path.join(work_dir, "transcript_vi.json"))

    # --- Step 5: TTS ---
    logger.info("=" * 60)
    logger.info("STEP 5: Synthesizing Vietnamese audio (VieNeu TTS)")
    seg_dir = ensure_dir(os.path.join(work_dir, "segments"))
    from src.synthesizer_vieneu import synthesize_segment_vieneu

    tts_results = []
    for seg in segments:
        seg_path = os.path.join(seg_dir, f"seg_{seg['id']:03d}.wav")
        result = synthesize_segment_vieneu(
            text_vi=seg["text_vi"], output_path=seg_path,
            target_duration=seg["duration"], voice=voice_id,
        )
        logger.info(f"  Segment {seg['id']}: {result['actual_duration']:.1f}s (target: {seg['duration']:.1f}s)")
        tts_results.append(result)

    # --- Step 6: Merge audio ---
    logger.info("=" * 60)
    logger.info("STEP 6: Fitting timeline & merging audio")
    slow_factor = config.AUDIO_SLOW_FACTOR
    total_duration = max(seg["end"] for seg in segments) + 1.0 if segments else 0

    if slow_factor < 1.0:
        slow_pct = round((1.0 - slow_factor) * 100)
        slow_dir = ensure_dir(os.path.join(work_dir, f"segments_slow{slow_pct}"))
        for seg in segments:
            src = os.path.join(seg_dir, f"seg_{seg['id']:03d}.wav")
            dst = os.path.join(slow_dir, f"seg_{seg['id']:03d}.wav")
            if os.path.exists(src):
                subprocess.run(
                    [FFMPEG_PATH, "-y", "-i", src, "-filter:a", f"atempo={slow_factor}", dst],
                    capture_output=True, text=True,
                )
        pre_fit_dir = slow_dir
    else:
        pre_fit_dir = seg_dir

    fit_dir = ensure_dir(os.path.join(work_dir, "segments_fit"))
    fit_segments_to_timeline(segments, pre_fit_dir, fit_dir)

    merged_audio_path = os.path.join(work_dir, "audio_vi_full.wav")
    merge_segments(
        segments, fit_dir, merged_audio_path, total_duration,
        background_path=background_path, background_gain_db=background_gain_db,
    )

    # --- Step 7: Merge video ---
    logger.info("=" * 60)
    logger.info("STEP 7: Creating dubbed video")
    dubbed_video_path = os.path.join(work_dir, "dubbed_video.mp4")
    merge_video(file_path, merged_audio_path, dubbed_video_path)

    if burn_sub:
        dubbed_video_path = _burn_subtitle(dubbed_video_path, segments, sub_style, work_dir)

    # Cleanup intermediate files
    import shutil
    for entry_name in os.listdir(work_dir):
        entry_path = os.path.join(work_dir, entry_name)
        if os.path.isdir(entry_path) and (
            entry_name in ("segments", "segments_fit", "vocals.wav")
            or entry_name.startswith("segments_slow")
        ):
            shutil.rmtree(entry_path, ignore_errors=True)

    elapsed = time.time() - start_time
    report = {
        "session_id": folder_name,
        "source_language": lang_code,
        "target_language": "vi-VN",
        "voice_id": voice_id,
        "total_segments": len(segments),
        "total_original_duration": round(sum(s["duration"] for s in segments), 3),
        "total_tts_duration": round(sum(r["actual_duration"] for r in tts_results), 3),
        "processing_time_seconds": round(elapsed, 1),
        "output_dir": work_dir,
        "files": {"dubbed_video": dubbed_video_path},
    }
    with open(os.path.join(work_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info(f"  Output:    {work_dir}")
    logger.info(f"  Segments:  {report['total_segments']}")
    logger.info(f"  Time:      {elapsed:.1f}s")
    logger.info("=" * 60)
    return report

def cleanup_old_outputs(output_dir: str, keep_hours: float = 2.0) -> None:
    """Xoá folder output cũ hơn keep_hours giờ, dựa vào mtime của
    dubbed_video.mp4 bên trong (không dùng mtime folder — Windows tự làm
    mới mtime folder mỗi khi có file con bị ghi/xoá)."""
    import shutil
    now = time.time()
    cutoff = now - keep_hours * 3600

    if not os.path.isdir(output_dir):
        return

    for folder in os.listdir(output_dir):
        folder_path = os.path.join(output_dir, folder)
        if not os.path.isdir(folder_path):
            continue

        reference_time = None
        video_path = os.path.join(folder_path, "dubbed_video.mp4")
        if os.path.exists(video_path):
            reference_time = os.path.getmtime(video_path)
        else:
            report_path = os.path.join(folder_path, "report.json")
            if os.path.exists(report_path):
                reference_time = os.path.getmtime(report_path)
            else:
                reference_time = os.path.getmtime(folder_path)

        if reference_time > cutoff:
            continue

        try:
            shutil.rmtree(folder_path, ignore_errors=True)
            logger.info(f"Auto-cleanup: removed {folder_path}")
        except Exception as e:
            logger.warning(f"Auto-cleanup failed: {folder_path} — {e}")