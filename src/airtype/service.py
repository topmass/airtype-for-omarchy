import json
import queue
import sys
import threading
import time
from dataclasses import asdict

from .hotkey import format_hotkey, parse_hotkey_keys, start_global_hotkey_listener
from .pipeline import AirtypePipeline

DOUBLE_TAP_THRESHOLD = 0.3
STOP_COOLDOWN = 0.25
LISTENER_HEALTH_INTERVAL = 2.0
RESUME_GAP_THRESHOLD = 10.0


class AirtypeService:
    def __init__(
        self,
        paste_mode: str,
        unload_timeout_seconds: float,
        hotkey: str | None,
        copy: bool = True,
    ) -> None:
        self.pipeline = AirtypePipeline(
            paste_mode=paste_mode,
            copy=copy,
            unload_timeout_seconds=unload_timeout_seconds,
        )
        self.hotkey_keys = parse_hotkey_keys(hotkey)
        self._state = "ready"
        self._session = None
        self._last_hotkey_time = 0.0
        self._stop_armed_at = 0.0
        self._pressed_hotkey_keys: set[str] = set()
        self._hotkey_combo_active = False
        self._listener = None
        self._listener_backend = "unavailable"
        self._listener_checked_at = 0.0
        self._last_loop_at = time.time()
        self._commands: queue.Queue[str] = queue.Queue()
        self._lock = threading.RLock()
        self._closed = threading.Event()

    def run(self) -> int:
        self._start_listener()
        self._emit(
            "ready",
            state=self._state,
            hotkey=format_hotkey(self.hotkey_keys),
            listener=self._listener_backend,
            model_loaded=self.pipeline.manager.is_loaded(),
        )

        reader = threading.Thread(target=self._read_commands, daemon=True)
        reader.start()

        try:
            while not self._closed.is_set():
                try:
                    command = self._commands.get(timeout=0.2)
                except queue.Empty:
                    self._restart_listener_after_resume_gap()
                    self._restart_listener_if_dead()
                    continue
                if command == "quit":
                    self.close()
                    break
                if command == "toggle":
                    self.toggle()
        finally:
            self.close()
        return 0

    def _start_listener(self) -> None:
        self._listener, self._listener_backend = start_global_hotkey_listener(
            self._on_press_name,
            self._on_release_name,
            self.hotkey_keys,
        )
        self._listener_checked_at = time.time()

    def _listener_alive(self) -> bool:
        if self._listener is None:
            return False
        is_alive = getattr(self._listener, "is_alive", None)
        if callable(is_alive):
            return bool(is_alive())
        return True

    def _restart_listener_if_dead(self) -> None:
        now = time.time()
        if now - self._listener_checked_at < LISTENER_HEALTH_INTERVAL:
            return
        self._listener_checked_at = now
        if self._listener_alive():
            return

        self._restart_listener(f"Global listener restarted: {self._listener_backend}")

    def _restart_listener_after_resume_gap(self) -> None:
        now = time.time()
        gap = now - self._last_loop_at
        self._last_loop_at = now
        if gap < RESUME_GAP_THRESHOLD:
            return

        self._restart_listener(f"Global listener refreshed after resume: {self._listener_backend}")

    def _restart_listener(self, message: str) -> None:
        self._stop_listener()
        self._pressed_hotkey_keys.clear()
        self._hotkey_combo_active = False
        self._last_hotkey_time = 0.0
        self._start_listener()
        self._emit(
            "listener",
            state=self._state,
            hotkey=format_hotkey(self.hotkey_keys),
            listener=self._listener_backend,
            message=message,
        )

    def _stop_listener(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def close(self) -> None:
        self._closed.set()
        self._stop_listener()
        with self._lock:
            if self._state == "recording":
                self._stop_recording()

    def toggle(self) -> None:
        with self._lock:
            if self._state == "recording" and time.time() >= self._stop_armed_at:
                self._stop_recording()
            elif self._state in {"ready", "unloaded"}:
                self._start_recording()

    def _read_commands(self) -> None:
        for line in sys.stdin:
            command = line.strip().lower()
            if not command:
                continue
            try:
                data = json.loads(command)
            except json.JSONDecodeError:
                data = {"command": command}
            self._commands.put(str(data.get("command", "")).lower())

    def _on_press_name(self, key_name: str) -> None:
        with self._lock:
            self._pressed_hotkey_keys.add(key_name)
            combo_active = set(self.hotkey_keys).issubset(self._pressed_hotkey_keys)
            if combo_active and not self._hotkey_combo_active:
                self._hotkey_combo_active = True
                self._handle_hotkey_activation(time.time())

    def _on_release_name(self, key_name: str) -> None:
        with self._lock:
            self._pressed_hotkey_keys.discard(key_name)
            combo_active = set(self.hotkey_keys).issubset(self._pressed_hotkey_keys)
            if self._hotkey_combo_active and not combo_active:
                self._hotkey_combo_active = False

    def _handle_hotkey_activation(self, now: float) -> None:
        if self._state == "recording":
            if now >= self._stop_armed_at:
                self._stop_recording()
            self._last_hotkey_time = 0.0
        elif self._state in {"ready", "unloaded"}:
            if now - self._last_hotkey_time < DOUBLE_TAP_THRESHOLD:
                self._start_recording()
                self._last_hotkey_time = 0.0
            else:
                self._last_hotkey_time = now

    def _start_recording(self) -> None:
        self._session = self.pipeline.start_recording()
        self._state = "recording"
        self._stop_armed_at = time.time() + STOP_COOLDOWN
        self._emit("state", state=self._state, message="Recording")

    def _stop_recording(self) -> None:
        session = self._session
        if session is None:
            return
        self._session = None
        self._state = "transcribing"
        self._emit("state", state=self._state, message="Transcribing")
        result = self.pipeline.stop_recording(session)
        self._state = "unloaded" if not self.pipeline.manager.is_loaded() else "ready"
        self._emit("transcript", state=self._state, result=asdict(result))

    def _emit(self, event: str, **data) -> None:
        payload = {"event": event, **data}
        print(json.dumps(payload), flush=True)
