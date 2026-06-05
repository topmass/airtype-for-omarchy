import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import numpy as np

from airtype import asr
from airtype import cli
from airtype import config
from airtype.hotkey import format_hotkey, parse_hotkey_keys
from airtype.clipboard import normalize_paste_mode
from airtype.model_manager import ModelManager
from airtype.pipeline import RecordingSession, SAMPLE_RATE, TranscriptResult
from airtype.service import AirtypeService


class FakeResult:
    text = "hello world"


class FakeStream:
    result = FakeResult()

    def accept_waveform(self, sample_rate, samples) -> None:
        self.sample_rate = sample_rate
        self.samples = samples


class FakeSherpaModel:
    def __init__(self) -> None:
        self.streams = []

    def create_stream(self):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def decode_stream(self, stream) -> None:
        self.decoded = [stream]

    def decode_streams(self, streams) -> None:
        self.decoded = streams


class SlowBorrowManager:
    def __init__(self) -> None:
        self.model = FakeSherpaModel()
        self.loaded = False

    @contextmanager
    def borrow(self):
        time.sleep(0.05)
        loaded_now = not self.loaded
        self.loaded = True
        yield self.model, loaded_now

    def touch(self) -> None:
        pass

    def unload_if_idle(self, timeout_seconds: float) -> bool:
        self.loaded = False
        return True


class FakeInputStream:
    def __init__(self, channels, samplerate, dtype, callback) -> None:
        self.callback = callback
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        pass


class FakeManager:
    def __init__(self) -> None:
        self.loaded = False

    def is_loaded(self) -> bool:
        return self.loaded


class FakePipeline:
    def __init__(self) -> None:
        self.manager = FakeManager()
        self.started = 0
        self.stopped = 0

    def start_recording(self):
        self.started += 1
        return object()

    def stop_recording(self, session):
        self.stopped += 1
        return TranscriptResult("hello", True, True, "ydotool", 0.1, False, True)


class FakeSession:
    def stop(self):
        return "hello", False


class FakeHotkeyListener:
    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.stopped = False

    def is_alive(self) -> bool:
        return self.alive

    def stop(self) -> None:
        self.stopped = True


class AirtypeTests(unittest.TestCase):
    def test_paste_mode_accepts_cli_spelling(self) -> None:
        self.assertEqual(normalize_paste_mode("ctrl-shift-v"), "ctrl_shift_v")
        self.assertEqual(normalize_paste_mode("copy"), "copy_only")

    def test_hotkey_parser_formats_aliases(self) -> None:
        keys = parse_hotkey_keys("control+alt_l")

        self.assertEqual(keys, ["ctrl", "alt"])
        self.assertEqual(format_hotkey(keys), "Ctrl+Alt")

    def test_default_model_dir_uses_configured_cache_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = Path(tmpdir) / "models" / asr.DEFAULT_MODEL

            with mock.patch.object(asr, "configured_model_dir", return_value=model_dir), mock.patch.dict(
                "os.environ", {}, clear=True
            ):
                self.assertEqual(asr.default_model_dir(), model_dir)

    def test_model_dir_env_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            "os.environ", {"AIRTYPE_MODEL_DIR": tmpdir}, clear=True
        ):
            self.assertEqual(asr.default_model_dir(), Path(tmpdir))

    def test_settings_can_store_custom_model_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            model_parent = Path(tmpdir) / "airtype-models"
            with mock.patch.object(config, "CONFIG_PATH", config_path):
                self.assertEqual(cli.main(["settings", "--model-dir", str(model_parent), "--json"]), 0)
                saved = config.load_config()

        self.assertEqual(saved["model_dir"], str(model_parent / asr.DEFAULT_MODEL))

    def test_ensure_model_falls_back_to_hugging_face_files(self) -> None:
        downloaded = []

        def fake_urlretrieve(url, target):
            downloaded.append(url)
            if url == asr.MODEL_URL:
                raise OSError("primary down")
            Path(target).write_text("model")

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "urllib.request.urlretrieve", side_effect=fake_urlretrieve
        ):
            asr._ensure_model(Path(tmpdir) / asr.DEFAULT_MODEL)

        self.assertIn(asr.MODEL_URL, downloaded)
        self.assertIn(
            f"https://huggingface.co/{asr.HF_MODEL_REPO}/resolve/main/tokens.txt",
            downloaded,
        )

    def test_cli_defaults_to_record_command(self) -> None:
        with mock.patch.object(cli, "_ensure_model_ready", return_value=True), mock.patch.object(
            cli, "_record", return_value=0
        ) as record:
            self.assertEqual(cli.main([]), 0)

        record.assert_called_once()

    def test_noninteractive_missing_model_does_not_download(self) -> None:
        with mock.patch.object(cli, "model_exists", return_value=False), mock.patch.object(
            cli, "_setup", return_value=0
        ) as setup:
            self.assertFalse(cli._ensure_model_ready(interactive=False))

        setup.assert_not_called()

    def test_service_double_tap_starts_then_single_tap_stops(self) -> None:
        service = AirtypeService("copy-only", 0, "alt", copy=False)
        service.pipeline = FakePipeline()
        service._emit = lambda *args, **kwargs: None

        with mock.patch("airtype.service.time.time", side_effect=[10.0, 10.2, 10.6]):
            service._handle_hotkey_activation(10.0)
            service._handle_hotkey_activation(10.2)
            service._handle_hotkey_activation(10.6)

        self.assertEqual(service.pipeline.started, 1)
        self.assertEqual(service.pipeline.stopped, 1)

    def test_service_restarts_dead_hotkey_listener(self) -> None:
        listeners = [FakeHotkeyListener(False), FakeHotkeyListener(True)]
        service = AirtypeService("copy-only", 0, "alt", copy=False)
        service._emit = lambda *args, **kwargs: None

        with mock.patch(
            "airtype.service.start_global_hotkey_listener",
            side_effect=[(listeners[0], "evdev"), (listeners[1], "evdev")],
        ):
            service._start_listener()
            service._listener_checked_at = 0.0
            with mock.patch("airtype.service.time.time", return_value=10.0):
                service._restart_listener_if_dead()

        self.assertTrue(listeners[0].stopped)
        self.assertIs(service._listener, listeners[1])

    def test_service_refreshes_hotkey_listener_after_resume_gap(self) -> None:
        listeners = [FakeHotkeyListener(True), FakeHotkeyListener(True)]
        service = AirtypeService("copy-only", 0, "alt", copy=False)
        service._emit = lambda *args, **kwargs: None

        with mock.patch(
            "airtype.service.start_global_hotkey_listener",
            side_effect=[(listeners[0], "evdev"), (listeners[1], "evdev")],
        ):
            service._start_listener()
            service._last_loop_at = 1.0
            with mock.patch("airtype.service.time.time", return_value=20.0):
                service._restart_listener_after_resume_gap()

        self.assertTrue(listeners[0].stopped)
        self.assertIs(service._listener, listeners[1])

    def test_sherpa_transcribe_array_batches_long_audio(self) -> None:
        audio = np.zeros(SAMPLE_RATE * 35, dtype=np.float32)
        model = FakeSherpaModel()

        with mock.patch.dict("os.environ", {"AIRTYPE_SHERPA_CHUNK_SECONDS": "30"}):
            text = asr.transcribe_array(model, audio, SAMPLE_RATE)

        self.assertEqual(text, "hello world hello world")
        self.assertEqual(len(model.decoded), 2)

    def test_model_manager_unloads_immediately(self) -> None:
        manager = ModelManager()
        manager._model = object()
        manager._last_used_at = 0

        self.assertTrue(manager.unload_if_idle(0))
        self.assertFalse(manager.is_loaded())

    def test_recording_captures_audio_while_model_loads(self) -> None:
        manager = SlowBorrowManager()
        session = RecordingSession(manager)
        chunk = np.ones((SAMPLE_RATE, 1), dtype=np.float32) * 0.01

        with mock.patch("sounddevice.InputStream", FakeInputStream):
            session.start()
            session._audio_cb(chunk, len(chunk), None, None)
            time.sleep(0.01)
            self.assertGreater(len(session._snapshot_audio()), 0)
            text, loaded_now = session.stop()

        self.assertTrue(loaded_now)
        self.assertEqual(text, "hello world")

    def test_pipeline_plays_start_and_stop_sounds_for_recording(self) -> None:
        manager = SlowBorrowManager()

        with mock.patch("sounddevice.InputStream", FakeInputStream), mock.patch(
            "airtype.pipeline.play_sound"
        ) as play_sound, mock.patch("airtype.pipeline.play_sound_later") as play_sound_later:
            pipeline = cli.AirtypePipeline(manager=manager, copy=False)
            session = pipeline.start_recording()
            session._audio_cb(np.ones((SAMPLE_RATE, 1), dtype=np.float32) * 0.01, SAMPLE_RATE, None, None)
            result = pipeline.stop_recording(session)

        self.assertEqual(result.text, "hello world")
        play_sound.assert_called_once()
        play_sound_later.assert_called_once()

    def test_stop_sound_plays_after_copy_and_paste_finish(self) -> None:
        events = []

        def fake_copy(text):
            events.append("copy")
            return True

        def fake_paste(mode):
            events.append("paste")
            return True, "ydotool"

        def fake_sound(path):
            events.append("sound")

        with mock.patch("airtype.pipeline.copy_to_clipboard", side_effect=fake_copy), mock.patch(
            "airtype.pipeline.auto_paste", side_effect=fake_paste
        ), mock.patch("airtype.pipeline.play_sound_later", side_effect=fake_sound):
            result = cli.AirtypePipeline(
                manager=SlowBorrowManager(),
                paste_mode="ctrl-shift-v",
            ).stop_recording(FakeSession())

        self.assertTrue(result.pasted)
        self.assertEqual(events, ["copy", "paste", "sound"])

    def test_find_segment_end_uses_pause_boundary(self) -> None:
        session = RecordingSession(SlowBorrowManager())
        speech = np.ones(SAMPLE_RATE * 7, dtype=np.float32) * 0.02
        silence = np.zeros(int(SAMPLE_RATE * 0.8), dtype=np.float32)
        audio = np.concatenate([speech, silence])

        end = session._find_segment_end(audio, 0)

        self.assertIsNotNone(end)
        self.assertGreaterEqual(end, len(speech))


if __name__ == "__main__":
    unittest.main()
