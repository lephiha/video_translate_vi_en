"""Desktop launcher — chạy FastAPI server ngầm rồi mở cửa sổ pywebview.

Lần đầu chạy (chưa có model VieNeu trong cache): hiện Setup Wizard tải
model trước, có progress bar thật, KHÔNG chặn app nếu lỗi (non-fatal).
Các lần sau: model đã có sẵn trong cache → vào thẳng app chính, không qua
wizard.

QUAN TRỌNG: webview.start() chỉ được gọi ĐÚNG MỘT LẦN trong toàn bộ đời
sống process. Nếu cần chuyển từ màn hình Setup sang app chính, dùng
window.load_url() trên CHÍNH cửa sổ đang có — không tạo cửa sổ mới, không
gọi start() lần 2. Xem thêm lịch sử bug ở src/first_run_setup.py.
"""
import os
import sys
import threading
import time
import traceback

import certifi
import uvicorn
import webview

# Ép SSL cert bundle đúng (certifi) + timeout hợp lý cho HF Hub download —
# thiếu cái này trên máy đã đóng gói PyInstaller, request tải model có thể
# treo vô hạn thay vì báo lỗi rõ ràng.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

# APP_DATA_DIR: nơi lưu model/cache bền qua các lần chạy — không dùng thư
# mục cài đặt (có thể là Program Files, không có quyền ghi cho user thường).
_APP_DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "VideoDubEnVi"
)
os.makedirs(_APP_DATA_DIR, exist_ok=True)
os.environ["APP_DATA_DIR"] = _APP_DATA_DIR

# Ghi log ra file cạnh exe — console=False khiến print()/traceback không
# hiện ra đâu cả, phải tự ghi file mới debug được lỗi khởi động.
_log_path = os.path.join(
    os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__),
    "debug.log",
)


def _log(msg: str):
    try:
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _run_server():
    try:
        _log("Starting uvicorn...")
        uvicorn.run("server:app", host="127.0.0.1", port=8080, reload=False,
                    ws_ping_interval=60, ws_ping_timeout=None, timeout_keep_alive=3600,
                    log_level="info")
    except Exception:
        _log("SERVER CRASHED:\n" + traceback.format_exc())


def _vieneu_model_cached() -> bool:
    """Kiểm tra model VieNeu đã có trong cache local chưa — quyết định có
    cần hiện Setup Wizard hay vào thẳng app chính."""
    from src.synthesizer_vieneu import is_model_cache_ready

    model_dir = os.path.join(
        _APP_DATA_DIR, "vieneu_models", "hub",
        "models--pnnbao-ump--VieNeu-TTS-v3-Turbo",
    )
    return is_model_cache_ready(model_dir)


def main():
    _log("=== App start ===")

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()
    time.sleep(2.5)  # đợi server bind port trước khi mở cửa sổ

    main_url = "http://127.0.0.1:8080"
    window_holder = {}

    if _vieneu_model_cached():
        _log("Model VieNeu đã có sẵn trong cache — vào thẳng app chính.")
        window = webview.create_window(
            "Video Dub EN→VI", main_url,
            width=980, height=900, min_size=(760, 640),
        )
        webview.start()
        return

    # Lần đầu chạy — hiện Setup Wizard trước.
    from src.first_run_setup import build_setup_html, SetupAPI

    def _on_setup_complete(window):
        _log("Setup hoàn tất — chuyển sang app chính.")
        try:
            window.load_url(main_url)
        except Exception:
            _log("load_url thất bại:\n" + traceback.format_exc())

    api = SetupAPI(window_holder, _on_setup_complete)

    window = webview.create_window(
        "Video Dub EN→VI — Thiết lập lần đầu",
        html=build_setup_html(),
        js_api=api,
        width=520, height=420, min_size=(480, 380),
    )
    window_holder["window"] = window

    webview.start(api.start)


if __name__ == "__main__":
    main()
