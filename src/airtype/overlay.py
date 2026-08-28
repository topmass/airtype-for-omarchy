"""Spawns the waveform overlay (overlay_app.py) and streams mic levels to it.

The overlay needs PyGObject + gtk4-layer-shell, which are system packages, so
it always runs under the system python3 rather than the airtype venv. Missing
pieces (no system python, no Wayland, GTK import failure in the child) degrade
gracefully: recording works exactly as before, just without the visual.
"""

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

OVERLAY_APP_PATH = Path(__file__).with_name("overlay_app.py")
STOP_GRACE_SECONDS = 1.0


def system_python() -> str | None:
    if os.access("/usr/bin/python3", os.X_OK):
        return "/usr/bin/python3"
    return shutil.which("python3")


class WaveformOverlay:
    """One overlay process per recording; feed() is safe from audio threads."""

    def __init__(self, log: Callable[[str], None] = print, debug: bool = False) -> None:
        self._proc: subprocess.Popen | None = None
        self._log = log
        self._debug = debug
        self._warned = False

    def start(self) -> None:
        if self._proc is not None:
            return
        python = system_python()
        if python is None or not os.environ.get("WAYLAND_DISPLAY"):
            self._warn_once("waveform overlay unavailable (needs system python3 and Wayland)")
            return
        try:
            proc = subprocess.Popen(
                [python, str(OVERLAY_APP_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=None if self._debug else subprocess.DEVNULL,
            )
        except OSError as exc:
            self._warn_once(f"waveform overlay failed to start: {exc}")
            return
        # feed() runs on the PortAudio callback thread and must never block.
        os.set_blocking(proc.stdin.fileno(), False)
        self._proc = proc

    def feed(self, rms: float) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(f"{rms:.5f}\n".encode())
            proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        # EOF triggers the overlay's fade-out; reap off the main loop so
        # stopping a recording never waits on the overlay process.
        threading.Thread(target=_reap, args=(proc,), daemon=True).start()

    def _warn_once(self, message: str) -> None:
        if not self._warned:
            self._warned = True
            self._log(message)


def _reap(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
