import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from airtype import overlay as overlay_module
from airtype.overlay import WaveformOverlay


class WaveformOverlayTests(unittest.TestCase):
    def test_feed_and_stop_are_safe_without_start(self) -> None:
        overlay = WaveformOverlay(log=lambda message: None)
        overlay.feed(0.1)
        overlay.stop()
        self.assertIsNone(overlay._proc)

    def test_start_warns_once_when_unavailable(self) -> None:
        messages: list[str] = []
        overlay = WaveformOverlay(log=messages.append)
        with mock.patch.object(overlay_module, "system_python", return_value=None):
            overlay.start()
            overlay.start()
        self.assertIsNone(overlay._proc)
        self.assertEqual(len(messages), 1)

    def test_start_feed_stop_roundtrip(self) -> None:
        # Stand-in overlay app: drains stdin until EOF, like the real one.
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as script:
            script.write("import sys\nsys.stdin.buffer.read()\n")
        script_path = Path(script.name)
        self.addCleanup(script_path.unlink)

        overlay = WaveformOverlay(log=lambda message: None)
        with (
            mock.patch.object(overlay_module, "system_python", return_value=sys.executable),
            mock.patch.object(overlay_module, "OVERLAY_APP_PATH", script_path),
            mock.patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-test"}),
        ):
            overlay.start()
            self.assertIsNotNone(overlay._proc)
            proc = overlay._proc
            for _ in range(5):
                overlay.feed(0.123)
            overlay.stop()

        self.assertIsNone(overlay._proc)
        deadline = time.monotonic() + 3
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(proc.poll(), 0)
