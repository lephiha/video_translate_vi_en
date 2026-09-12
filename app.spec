# -*- mode: python ; coding: utf-8 -*-
"""
app.spec — PyInstaller build cho Video Dub EN→VI.

Build (Windows, trong venv đã cài requirements.txt + pyinstaller):
    pyinstaller app.spec --noconfirm

Output: dist/VideoDubEnVi/VideoDubEnVi.exe

LƯU Ý:
- KHÔNG dùng collect_all() cho torch/torchaudio/transformers/demucs — các
  package này chứa hàng chục nghìn file test/tool/distributed khiến
  PyInstaller phân loại cực chậm, có thể "treo" hàng giờ. Chỉ khai qua
  hiddenimports + collect_dynamic_libs/collect_data_files có chọn lọc.
- sherpa_onnx tự mang một bản onnxruntime.dll riêng trong package của nó.
  Nếu để nguyên, app sẽ nạp CÙNG LÚC 2 bản onnxruntime (1 từ sherpa_onnx,
  1 từ package onnxruntime) → lỗi "CPU dispatcher tracer already
  initialized" lúc chạy exe. Phải lọc DLL trùng này ra khỏi a.binaries
  (xem đoạn filter phía dưới, sau Analysis).
- Model AI (VieNeu, sherpa-onnx, NLLB) KHÔNG bundle vào exe — tự tải về
  lúc chạy lần đầu, giữ installer nhẹ. Máy cài cần mạng ở lần chạy đầu.
- upx=False: UPX từng gây lỗi với DLL của onnxruntime.
"""
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs
import imageio_ffmpeg

block_cipher = None

datas = []
binaries = []
hiddenimports = []


def _add(pkg: str, required: bool = False):
    """collect_all(pkg) rồi gộp vào datas/binaries/hiddenimports.
    required=False: package không cài / lỗi collect → bỏ qua, in cảnh báo,
    không làm vỡ cả build vì 1 package optional."""
    global datas, binaries, hiddenimports
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
        print(f"[app.spec] collected: {pkg} ({len(d)} datas, {len(b)} binaries)")
    except Exception as e:
        msg = f"[app.spec] KHÔNG collect được '{pkg}': {e}"
        if required:
            raise RuntimeError(msg)
        print("[app.spec] (bỏ qua, optional) " + msg)


# ── Package nhẹ, an toàn để collect_all trọn gói ──
_add("sherpa_onnx", required=True)     # ASR fallback — compiled extension + data
_add("webview", required=True)          # pywebview
_add("vieneu", required=True)           # TTS — model tự tải, nhưng code/data gói cần đủ
_add("sea_g2p")   # phonemizer nội bộ của vieneu — thiếu sẽ gây lỗi file not found khó hiểu
_add("certifi", required=True)
# ── Bundle sẵn model VieNeu đã tải từ máy dev — máy đích khỏi cần tải mạng ──
import os as _os

# collect_all(imageio_ffmpeg) không luôn nhận binary tải kèm trên Windows.
# Thêm thẳng executable để chắc chắn bản onedir chạy được trên máy không cài FFmpeg.
_ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
binaries.append((_ffmpeg_exe, "."))
print(f"[app.spec] bundled FFmpeg executable: {_ffmpeg_exe}")

_add("fastapi", required=True)
_add("starlette", required=True)
_add("uvicorn", required=True)

# ── Package NẶNG — KHÔNG collect_all (xem cảnh báo ở docstring) ──
# Chỉ lấy data/DLL cần thiết bằng hook có chọn lọc + khai hiddenimports tay.
datas += collect_data_files("demucs")          # danh sách pretrained model
binaries += collect_dynamic_libs("onnxruntime")
binaries += collect_dynamic_libs("soundfile")

hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "webview.platforms.winforms",
    "groq",
    "openai",
    "numpy",
    "numpy._core._exceptions",
    "onnxruntime",
    "soundfile",
    "pydub",
    "torch",
    "torchaudio",
    "transformers",
    "demucs.apply",
    "demucs.audio",
    "demucs.pretrained",
    "google.generativeai",
]

# ── Data của chính project ──
datas += [
    ("src", "src"),
    ("config.py", "."),
    ("pipeline.py", "."),
    ("server.py", "."),
    ("static", "static"),
    ("voices", "voices"),
    (".env", "."),
    (".env.example", "."),
]

a = Analysis(
    ["desktop.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Lọc onnxruntime.dll riêng của sherpa_onnx — tránh nạp trùng 2 bản
# onnxruntime cùng lúc (xem cảnh báo ở docstring đầu file).
a.binaries = [
    entry for entry in a.binaries
    if not entry[1].replace("\\", "/").lower().endswith("sherpa_onnx/lib/onnxruntime.dll")
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoDubEnVi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # False vì UPX từng gây lỗi DLL onnxruntime
    console=False,         # đổi True khi cần xem log lỗi khởi động (debug)
    icon="docs/app_icon.ico",
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VideoDubEnVi",
)
