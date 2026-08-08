# Video Dub EN→VI

Lồng tiếng Việt tự động cho video tiếng Anh có sẵn trên máy (không crawl link).

## Sơ đồ luồng xử lý

```
Video local (.mp4/.mov/.mkv/.avi/.webm)
      │  kéo-thả / chọn file trên UI
      ▼
POST /upload  → lưu vào uploads/, trả về file_id
      │
      ▼
WebSocket /ws/run  (gửi file_id + cấu hình)
      │
      ▼
STEP 1  Extract audio (ffmpeg → wav 16kHz mono)
      │
      ▼
STEP 2  Tách nhạc nền (Demucs) hoặc duck âm gốc hoặc tắt
      │
      ▼
STEP 3  ASR — Groq Whisper (chính) → sherpa-onnx SenseVoice (fallback offline)
      │
      ▼
STEP 4  Dịch EN→VI
      │  mode "api":    OpenAI → Gemini → Groq → OpenRouter → (fallback) NLLB offline
      │  mode "offline": NLLB-200 offline thẳng, không cần mạng
      ▼
STEP 5  TTS — VieNeu sinh giọng đọc tiếng Việt cho từng segment
      │
      ▼
STEP 6  Fit timeline (nén nhẹ segment nào tràn giờ) + ghép audio với nền
      │
      ▼
STEP 7  Ghép audio vào video gốc (ffmpeg mux) + burn phụ đề (tuỳ chọn)
      │
      ▼
output/<timestamp>/dubbed_video.mp4  → stream qua WebSocket "done", tải về từ UI
```

Toàn bộ log mỗi STEP được đẩy real-time qua WebSocket, UI tự map vào stepper 7 bước.

## Cài đặt

```bash
cd video-dub-en-vi
pip install -r requirements.txt --break-system-packages
copy .env.example .env
```

Mở `.env`, điền **ít nhất 1** trong các key dịch (`OPENAI_API_KEY` / `GOOGLE_API_KEY` / `GROQ_API_KEY` / `OPENROUTER_API_KEY`) nếu muốn dùng chế độ dịch AI. Bỏ trống hết vẫn chạy được — pipeline tự rớt xuống NLLB offline.

`GROQ_API_KEY` bắt buộc nếu muốn dùng ASR qua Groq Whisper (nhanh, chính xác hơn sherpa-onnx). Không có thì tự fallback sang sherpa-onnx offline (tải model ~150MB lần đầu).

## Chạy app

**Desktop (cửa sổ riêng, khuyến nghị dùng hàng ngày):**
```bash
python desktop.py
```

**Web thuần (debug, hoặc chạy trên máy khác remote vào):**
```bash
python server.py
# mở http://localhost:8080
```

## Luồng sử dụng trên UI

1. **Kéo-thả hoặc bấm chọn video** ở khung upload — hỗ trợ mp4/mov/mkv/avi/webm.
2. **Chọn chế độ dịch**: AI (chất lượng cao hơn, cần key) hoặc Offline (NLLB, không cần mạng).
3. **Chọn giọng đọc** — tab Nam/Nữ, bấm "Nghe thử" để preview trước khi chạy. Giọng custom (voice clone) hiện ở đầu danh sách nếu đã khai `VIENEU_CUSTOM_VOICES`.
4. **Chọn xử lý nhạc nền**: Demucs (AI tách, chuẩn nhất nhưng chậm hơn) / Duck (giảm âm gốc, nhanh) / Tắt.
5. **(Tuỳ chọn) Bật burn phụ đề** — chọn style TikTok/Tin tức/Clean.
6. Bấm **▶ Chạy lồng tiếng** — theo dõi tiến trình qua stepper + log realtime.
7. Xong: xem preview video ngay trong UI, bấm **⬇ Tải video về máy**.

## Thêm giọng custom (voice clone)

1. Copy file wav mẫu giọng (5-15s, rõ tiếng) vào `voices/`.
2. Khai trong `.env`:
   ```
   VIENEU_CUSTOM_VOICES=minhanh:male:voices/minhanh_ref.wav,lananh:female:voices/lananh_ref.wav
   ```
3. Restart app — giọng mới xuất hiện đầu danh sách dropdown, đánh dấu ⭐.

## Nâng cấp chất lượng dịch offline

Mặc định dùng `facebook/nllb-200-distilled-1.3B`. Đổi qua `.env`:
```
NLLB_MODEL_NAME=facebook/nllb-200-3.3B   # chất lượng cao nhất, cần RAM/VRAM lớn (~13GB)
```

## Build installer (Windows)

Yêu cầu: Windows 64-bit, Python venv đã `pip install -r requirements.txt` (bao gồm `pyinstaller`), và [Inno Setup 6](https://jrsoftware.org/isdl.php) đã cài + `iscc.exe` có trong PATH.

```bat
build.bat
```

Script chạy tuần tự:
1. `pyinstaller app.spec` → đóng gói `desktop.py` + toàn bộ dependency (torch, transformers, demucs, sherpa-onnx, vieneu...) thành `dist/VideoDubEnVi/VideoDubEnVi.exe` (dạng thư mục, không dùng onefile vì model AI nặng, giải nén lại mỗi lần mở sẽ rất chậm).
2. `iscc installer.iss` → đóng gói thư mục đó thành `installer_output/VideoDubEnVi-Setup-1.0.0.exe`.

Chạy tay từng bước nếu cần debug:
```bat
pyinstaller app.spec --noconfirm
iscc installer.iss
```

**Lưu ý:**
- Model AI (VieNeu, sherpa-onnx, NLLB nếu dùng offline) **không** đóng gói sẵn trong installer — chúng tự tải về `%LOCALAPPDATA%`/thư mục app lúc chạy lần đầu. Máy cài đặt vẫn cần mạng ở lần chạy đầu tiên.
- Đổi version: sửa `MyAppVersion` trong `installer.iss`.
- Cần icon riêng: thêm file `.ico`, sửa dòng `icon=None` trong `app.spec` và set `SetupIconFile=` trong `installer.iss`.
- Nếu PyInstaller báo thiếu module lúc chạy exe (`ModuleNotFoundError`), thêm tên module vào `hiddenimports` trong `app.spec` rồi build lại.


```
video-dub-en-vi/
├── config.py           # đọc .env, cấu hình chung
├── pipeline.py          # điều phối 7 step (không download/upload mạng xã hội)
├── server.py             # FastAPI: /upload, /voices, /ws/run, /download
├── desktop.py            # launcher pywebview (chạy server ngầm + mở cửa sổ)
├── src/
│   ├── audio_extractor.py
│   ├── vocal_separator.py     # Demucs
│   ├── transcriber_groq.py    # ASR chính (Groq Whisper)
│   ├── transcriber_sherpa.py  # ASR fallback offline (sherpa-onnx)
│   ├── translator_offline.py  # NLLB / M2M100
│   ├── synthesizer_vieneu.py  # TTS
│   ├── audio_merger.py        # fit timeline + mix nhạc nền
│   ├── video_merger.py        # mux ffmpeg
│   ├── srt_generator.py
│   ├── utils.py
│   └── ffmpeg_path.py
├── static/index.html     # UI kéo-thả
├── app.spec               # PyInstaller build spec
├── installer.iss          # Inno Setup script → installer .exe
├── build.bat               # build.bat → chạy PyInstaller + Inno Setup
├── uploads/               # video tạm khi đang xử lý (tự xoá sau khi xong)
├── output/                # kết quả từng lần chạy, đặt tên theo timestamp
└── voices/                # file wav giọng custom
```
