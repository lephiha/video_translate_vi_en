import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class FfmpegPathTests(unittest.TestCase):
    def test_resolve_uses_bundled_binary_in_frozen_app(self):
        from src import ffmpeg_path

        with tempfile.TemporaryDirectory() as temp_dir:
            binary_dir = Path(temp_dir) / "imageio_ffmpeg" / "binaries"
            binary_dir.mkdir(parents=True)
            bundled = binary_dir / "ffmpeg-test-win64.exe"
            bundled.touch()

            with mock.patch.object(sys, "_MEIPASS", temp_dir, create=True):
                resolved = ffmpeg_path._resolve("FFMPEG_PATH", "ffmpeg")

        self.assertEqual(resolved, str(bundled))


class AudioExtractorTests(unittest.TestCase):
    def test_missing_ffmpeg_reports_tool_error_not_missing_audio(self):
        from src import audio_extractor

        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "input.mp4"
            video.touch()
            output = Path(temp_dir) / "audio.wav"

            with mock.patch.object(
                audio_extractor.subprocess,
                "run",
                side_effect=FileNotFoundError("ffmpeg.exe"),
            ):
                with self.assertRaisesRegex(RuntimeError, "FFmpeg.*không chạy được"):
                    audio_extractor.extract_audio(str(video), str(output))


class WritablePathTests(unittest.TestCase):
    def test_default_output_dir_is_inside_app_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(
                os.environ,
                {"APP_DATA_DIR": temp_dir},
                clear=False,
            ):
                os.environ.pop("OUTPUT_DIR", None)
                import config

                reloaded = importlib.reload(config)

        self.assertEqual(reloaded.OUTPUT_DIR, os.path.join(temp_dir, "output"))


class CustomVoiceDiscoveryTests(unittest.TestCase):
    def test_bundled_wav_is_discovered_without_env_configuration(self):
        from src import synthesizer_vieneu

        with tempfile.TemporaryDirectory() as temp_dir:
            voices_dir = Path(temp_dir) / "voices"
            voices_dir.mkdir()
            clone = voices_dir / "my_clone.wav"
            clone.touch()

            with (
                mock.patch.object(synthesizer_vieneu, "_cfg", return_value=""),
                mock.patch.object(sys, "_MEIPASS", temp_dir, create=True),
            ):
                voices = synthesizer_vieneu.list_custom_voices()

        self.assertIn(("my_clone", "unknown", str(clone)), voices)


class AppIconTests(unittest.TestCase):
    def test_pyinstaller_spec_uses_existing_app_icon(self):
        project_root = Path(__file__).resolve().parents[1]
        spec = (project_root / "app.spec").read_text(encoding="utf-8")

        self.assertTrue((project_root / "docs" / "app_icon.ico").is_file())
        self.assertIn('icon="docs/app_icon.ico"', spec)


if __name__ == "__main__":
    unittest.main()
