"""Web UI server — Video Dub EN→VI (upload file local, không crawl)."""
import asyncio
import json
import logging
import os
import queue
import threading
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import config

app = FastAPI()

OUTPUT_DIR = config.OUTPUT_DIR
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")), name="static")


# ───────────────────────────── Upload ─────────────────────────────

ALLOWED_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTS:
        return {"success": False, "message": f"Định dạng {ext} không hỗ trợ. Dùng mp4/mov/mkv/avi/webm."}

    token = uuid.uuid4().hex[:12]
    dest_name = f"{token}{ext}"
    dest_path = os.path.join(UPLOAD_DIR, dest_name)

    with open(dest_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    return {"success": True, "file_id": dest_name, "original_name": file.filename}


@app.get("/download")
async def download_file(path: str):
    """Ép tải file về máy — pywebview không xử lý <a download> giống Chrome thật."""
    full_path = os.path.normpath(os.path.join(OUTPUT_DIR, path))
    if not full_path.startswith(os.path.normpath(OUTPUT_DIR)):
        return {"error": "Invalid path"}
    if not os.path.exists(full_path):
        return {"error": "File not found"}
    return FileResponse(full_path, media_type="application/octet-stream", filename=os.path.basename(full_path))


# ───────────────────────────── Voices ─────────────────────────────

@app.get("/voices")
async def get_voices():
    from fastapi.concurrency import run_in_threadpool
    try:
        from src.synthesizer_vieneu import list_all_voices
        return await run_in_threadpool(list_all_voices)
    except Exception as e:
        logging.getLogger("server").warning(f"Không load được VieNeu voices: {e}")
        return [{"label": "Giọng mặc định", "voice_id": "manhdung_ref", "kind": "preset",
                  "sample_id": "preset__manhdung_ref", "gender": "male", "is_default": True}]


@app.get("/voices/sample/{sample_id}")
async def get_voice_sample(sample_id: str):
    from fastapi.concurrency import run_in_threadpool
    from fastapi import HTTPException
    from src.synthesizer_vieneu import ensure_voice_sample
    path = await run_in_threadpool(ensure_voice_sample, sample_id)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Không tạo được sample")
    return FileResponse(path, media_type="audio/wav")


# ───────────────────────────── WebSocket run ─────────────────────────────

class WSLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put({"type": "log", "level": record.levelname, "message": self.format(record)})


@app.websocket("/ws/run")
async def ws_run(websocket: WebSocket):
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        params = json.loads(raw)
    except Exception as e:
        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        return

    file_id = params.get("file_id")
    if not file_id:
        await websocket.send_text(json.dumps({"type": "error", "message": "Chưa có file video."}))
        await websocket.close()
        return

    file_path = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.exists(file_path):
        await websocket.send_text(json.dumps({"type": "error", "message": "File upload không tồn tại (có thể server đã restart)."}))
        await websocket.close()
        return

    log_queue: queue.Queue = queue.Queue()
    ws_handler = WSLogHandler(log_queue)
    ws_handler.setFormatter(logging.Formatter("[%(asctime)s] %(name)s - %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(ws_handler)
    root_logger.setLevel(logging.INFO)

    result_holder = {}

    def _run():
        try:
            user_keys = params.get("api_keys", {})
            if user_keys.get("openai"):
                os.environ["OPENAI_API_KEY"] = user_keys["openai"]
            if user_keys.get("openrouter"):
                os.environ["OPENROUTER_API_KEY"] = user_keys["openrouter"]
            if user_keys.get("gemini1"):
                os.environ["GOOGLE_API_KEY"] = user_keys["gemini1"]
            if user_keys.get("gemini2"):
                os.environ["GOOGLE_API_KEY_2"] = user_keys["gemini2"]
            if user_keys.get("groq"):
                os.environ["GROQ_API_KEY"] = user_keys["groq"]

            from pipeline import run_pipeline
            result = run_pipeline(
                file_path=file_path,
                voice_id=params.get("voice_id"),
                output_dir=OUTPUT_DIR,
                source_lang=params.get("source_lang", "en"),
                bg_mode=params.get("bg", "demucs"),
                translate_mode=params.get("translate_mode", "api"),
                offline_model="nllb",
                burn_sub=params.get("burn_sub", False),
                sub_style=params.get("sub_style", "tiktok"),
            )
            result_holder["result"] = result
        except Exception as e:
            import traceback
            result_holder["error"] = str(e)
            logging.getLogger().error(traceback.format_exc())
        finally:
            try:
                os.remove(file_path)
            except Exception:
                pass
            log_queue.put({"type": "__done__"})

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    async def heartbeat():
        while thread.is_alive():
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break
            await asyncio.sleep(10)

    heartbeat_task = asyncio.create_task(heartbeat())

    while True:
        done = False
        while True:
            try:
                item = log_queue.get_nowait()
            except queue.Empty:
                break
            if item.get("type") == "__done__":
                done = True
                break
            try:
                await websocket.send_text(json.dumps(item))
            except Exception:
                pass
        if done or (not thread.is_alive() and log_queue.empty()):
            break
        await asyncio.sleep(0.1)

    heartbeat_task.cancel()
    root_logger.removeHandler(ws_handler)

    if "error" in result_holder:
        await websocket.send_text(json.dumps({"type": "error", "message": result_holder["error"]}))
        await websocket.close()
        return

    r = result_holder.get("result", {})
    dubbed_path = r.get("files", {}).get("dubbed_video", "")
    video_url = None
    video_filename = None
    if dubbed_path and os.path.exists(str(dubbed_path)):
        rel = os.path.relpath(dubbed_path, OUTPUT_DIR).replace("\\", "/")
        video_url = f"/output/{rel}"
        video_filename = os.path.basename(dubbed_path)

    await websocket.send_text(json.dumps({
        "type": "done",
        "segments": r.get("total_segments", 0),
        "duration": r.get("total_original_duration", 0),
        "vi_duration": r.get("total_tts_duration", 0),
        "time": r.get("processing_time_seconds", 0),
        "video_url": video_url,
        "video_filename": video_filename,
    }))
    await websocket.close()


# ───────────────────────────── Static index ─────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@app.on_event("startup")
async def _prewarm_vieneu_on_startup():
    def _warm():
        try:
            from src.synthesizer_vieneu import prewarm_vieneu
            if prewarm_vieneu():
                logging.getLogger("server").info("VieNeu đã sẵn sàng (prewarm xong).")
            else:
                logging.getLogger("server").error("VieNeu prewarm thất bại; TTS chưa sẵn sàng.")
        except Exception as e:
            logging.getLogger("server").warning(f"Prewarm VieNeu thất bại: {e}")
    threading.Thread(target=_warm, daemon=True).start()


if __name__ == "__main__":
    uvicorn.run("server:app", host="localhost", port=8080, reload=False,
                ws_ping_interval=60, ws_ping_timeout=None, timeout_keep_alive=3600)
