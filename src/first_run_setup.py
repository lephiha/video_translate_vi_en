"""
src/first_run_setup.py — Setup Wizard hiện khi chạy app lần đầu (tải model VieNeu).

QUAN TRỌNG — file này KHÔNG tự tạo cửa sổ và KHÔNG tự gọi webview.start().
pywebview chỉ cho phép gọi webview.start() ĐÚNG MỘT LẦN trong toàn bộ đời
sống của process. desktop.py là nơi DUY NHẤT tạo cửa sổ và gọi
webview.start() — nếu file này tự gọi start() lần nữa hoặc tự
create_window() lần nữa, kết quả là mở ra hàng chục cửa sổ liên tục.

File này chỉ cung cấp:
  - build_setup_html(): HTML để desktop.py nhúng khi tạo cửa sổ.
  - SetupAPI: logic tải model VieNeu qua Hugging Face Hub, expose cho JS
    qua js_api, dùng window.evaluate_js() để báo tiến trình lên cửa sổ mà
    desktop.py đang giữ. Khi xong, desktop.py sẽ window.load_url() sang
    app chính — KHÔNG tạo cửa sổ mới.

Khác với model ASR (sherpa-onnx, tải qua HTTP + tar.bz2), model VieNeu tải
qua huggingface_hub — thư viện đó tự lo caching/resume, nên không cần logic
resume thủ công phức tạp (curl -C -, watchdog giải nén...). Chỉ cần:
  - Set APP_DATA_DIR / HF cache TRƯỚC khi import vieneu (đã làm ở
    synthesizer_vieneu.py, không lặp lại ở đây).
  - Chạy trong thread riêng, báo progress giả lập theo thời gian (HF hub
    không expose progress callback dễ dùng qua API public).
  - KHÔNG chặn cả app nếu lỗi — non-fatal, log rõ, để lần dùng TTS đầu
    tiên tự thử tải lại (giống hệt cách app cũ xử lý bước TTS).
"""
import json
import logging
import threading
import time

logger = logging.getLogger("setup_wizard")

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<style>
  * { box-sizing: border-box; margin:0; padding:0; }
  body {
    font-family: 'Segoe UI', Inter, sans-serif;
    background: #0b0d10; color: #e6e9ee;
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; height: 100vh; padding: 40px;
  }
  .wrap { width: 100%; max-width: 480px; }
  h1 { font-size: 1.3rem; font-weight: 600; margin-bottom: 8px;
       background: linear-gradient(135deg,#5eead4,#60a5fa 60%);
       -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  .sub { font-size: 0.85rem; color: #7a8494; margin-bottom: 28px; }
  .step-label { display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 6px; }
  .step-label .pct { color: #5eead4; font-weight: 600; }
  .bar-bg { width: 100%; height: 8px; background: #191d24; border-radius: 4px; overflow: hidden; }
  .bar-fill { height: 100%; background: linear-gradient(90deg,#5eead4,#60a5fa); width: 0%; transition: width 0.3s; border-radius: 4px; }
  .done .bar-fill { background: #34d399; }
  .done .pct { color: #34d399; }
  .status-text { font-size: 0.78rem; color: #7a8494; margin-top: 8px; min-height: 16px; }
  .footer { margin-top: 32px; font-size: 0.78rem; color: #4b5563; text-align: center; }
  .error-box { display:none; background: #2a1515; border: 1px solid #fb7185; border-radius: 8px;
               padding: 14px; font-size: 0.82rem; color: #fb7185; margin-top: 16px; }
  .error-box.visible { display:block; }
  .retry-btn { margin-top: 10px; padding: 8px 16px; background: #5eead4; color: #0b0d10;
               border: none; border-radius: 6px; cursor: pointer; font-size: 0.82rem; font-weight: 600; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Đang chuẩn bị lần đầu chạy</h1>
  <p class="sub">Tải model giọng đọc tiếng Việt (chỉ chạy 1 lần). Vui lòng giữ kết nối mạng ổn định.</p>

  <div id="step-tts">
    <div class="step-label">
      <span>Tải model giọng đọc VieNeu</span>
      <span class="pct" id="pct-tts">0%</span>
    </div>
    <div class="bar-bg"><div class="bar-fill" id="bar-tts"></div></div>
    <div class="status-text" id="status-tts"></div>
  </div>

  <div class="error-box" id="error-box">
    <div id="error-msg"></div>
    <button class="retry-btn" onclick="retrySetup()">Thử lại</button>
  </div>

  <p class="footer">Video Dub EN-VI</p>
</div>

<script>
function updateProgress(stepId, percent, statusText) {
  const pct = Math.min(100, Math.max(0, percent));
  document.getElementById('pct-' + stepId).textContent = pct + '%';
  document.getElementById('bar-' + stepId).style.width = pct + '%';
  if (statusText) document.getElementById('status-' + stepId).textContent = statusText;
  if (pct >= 100) document.getElementById('step-' + stepId).classList.add('done');
}

function showError(message) {
  document.getElementById('error-msg').textContent = message;
  document.getElementById('error-box').classList.add('visible');
}

function hideError() {
  document.getElementById('error-box').classList.remove('visible');
}

function retrySetup() {
  hideError();
  window.pywebview.api.retry_setup();
}
</script>
</body>
</html>"""


def build_setup_html() -> str:
    """Trả về HTML hoàn chỉnh của Setup Wizard, để desktop.py nhúng trực tiếp
    khi tạo cửa sổ (qua tham số html=...)."""
    return _HTML_TEMPLATE


class SetupAPI:
    """API expose cho JS gọi ngược vào Python (qua pywebview js_api).

    window_holder: dict chứa {"window": <window đang hiện tại>}. Dùng dict
    (không truyền trực tiếp window) để desktop.py có thể gán window vào
    sau khi tạo xong (create_window trả về window, lúc build js_api chưa
    có window để truyền).
    """

    def __init__(self, window_holder: dict, on_complete):
        self._window_holder = window_holder
        self.on_complete = on_complete

    def retry_setup(self):
        threading.Thread(target=self._run, daemon=True).start()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _emit(self, percent: float, status: str = ""):
        window = self._window_holder.get("window")
        if window is None:
            return
        try:
            window.evaluate_js(
                f"updateProgress('tts', {percent}, {json.dumps(status, ensure_ascii=False)})"
            )
        except Exception as e:
            logger.warning(f"Lỗi update UI: {e}")

    def _emit_error(self, message: str):
        window = self._window_holder.get("window")
        if window is None:
            return
        try:
            window.evaluate_js(f"showError({json.dumps(message, ensure_ascii=False)})")
        except Exception:
            pass

    def _run(self):
        try:
            self._emit(5, "Đang kết nối Hugging Face...")

            # Progress giả lập theo thời gian chạy song song — HF hub không
            # expose progress callback dễ dùng qua API public của vieneu,
            # nên đây chỉ để người dùng thấy có tiến triển, không phải %
            # tải thật.
            stop_flag = threading.Event()

            def _fake_progress():
                elapsed = 0
                while not stop_flag.is_set() and elapsed < 240:
                    elapsed += 2
                    pct = min(92, 5 + elapsed // 3)
                    self._emit(pct, f"Đang tải model (lần đầu, có thể vài phút)... {elapsed}s")
                    time.sleep(2)

            progress_thread = threading.Thread(target=_fake_progress, daemon=True)
            progress_thread.start()

            from src.synthesizer_vieneu import prewarm_vieneu
            ok = prewarm_vieneu()

            stop_flag.set()

            if not ok:
                raise RuntimeError("Khởi tạo VieNeu thất bại — xem debug.log để biết chi tiết")

            self._emit(100, "Hoàn tất")

            window = self._window_holder.get("window")
            if window is not None and self.on_complete:
                self.on_complete(window)

        except Exception as e:
            logger.error(f"Setup thất bại: {e}", exc_info=True)
            self._emit_error(
                f"Lỗi: {e}. Kiểm tra kết nối mạng (cần truy cập được huggingface.co) rồi bấm Thử lại. "
                f"Nếu vẫn lỗi, app vẫn dùng được — model sẽ tự thử tải lại ở lần lồng tiếng đầu tiên."
            )
            # Non-fatal: vẫn cho vào app chính sau vài giây dù setup lỗi,
            # để không "nhốt" người dùng ở màn hình setup mãi mãi nếu mạng
            # có vấn đề tạm thời — lần TTS đầu tiên trong app sẽ tự thử tải lại.
            def _fallback_continue():
                time.sleep(6)
                window = self._window_holder.get("window")
                if window is not None and self.on_complete:
                    self.on_complete(window)
            threading.Thread(target=_fallback_continue, daemon=True).start()