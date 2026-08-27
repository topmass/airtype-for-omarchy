import os
import queue
import threading
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable

from . import __version__
from .config import CONFIG_PATH, load_config
from .hotkey import (
    HotkeyPolicy,
    format_hotkey,
    normalize_hotkey_key_name,
    parse_start_combo,
    start_global_hotkey_listener,
)
from .ipc import IPCServer
from .pipeline import AirtypePipeline

LISTENER_HEALTH_INTERVAL = 2.0
RESUME_GAP_THRESHOLD = 10.0
MODIFIER_RELEASE_WAIT = 1.0
DEBUG = os.environ.get("AIRTYPE_DEBUG") == "1"


class AirtypeService:
    """Always-on dictation service: evdev hotkeys + unix-socket control."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.pipeline = self._build_pipeline(self.config)
        self.policy = self._build_policy(self.config)
        self._state = "ready"
        self._session = None
        self._listener = None
        self._listener_backend = "unavailable"
        self._listener_checked_at = 0.0
        self._last_loop_at = time.time()
        self._started_at = time.time()
        self._commands: queue.Queue[tuple[dict, Callable[[dict], None] | None]] = queue.Queue()
        self._lock = threading.RLock()
        # Guards HotkeyPolicy state only. Listener threads must never need
        # self._lock, or key events would stall during transcription.
        self._policy_lock = threading.Lock()
        self._closed = threading.Event()
        self._ipc = IPCServer(self._submit_command)

    # -- construction helpers

    def _build_pipeline(self, config: dict[str, Any]) -> AirtypePipeline:
        return AirtypePipeline(
            paste_mode=config["paste_mode"],
            copy=True,
            unload_timeout_seconds=0,
            paste_fallback=config["paste_fallback"],
            terminal_classes=config["terminal_classes"],
            sounds_enabled=config["sounds_enabled"],
            pre_paste=self._wait_for_modifier_release,
        )

    def _build_policy(self, config: dict[str, Any]) -> HotkeyPolicy:
        modifiers, tap_key = parse_start_combo(config["start_hotkey"])
        stop_key = normalize_hotkey_key_name(config["stop_key"]) or tap_key
        return HotkeyPolicy(
            start_modifiers=modifiers,
            tap_key=tap_key,
            stop_key=stop_key,
            double_tap_threshold=config["double_tap_threshold"],
            stop_cooldown=config["stop_cooldown"],
            modifier_release_tolerance=config["super_release_tolerance"],
        )

    def _hotkey_keys(self) -> list[str]:
        keys = sorted(self.policy.start_modifiers) + [self.policy.tap_key]
        if self.policy.stop_key not in keys:
            keys.append(self.policy.stop_key)
        return keys

    # -- main loop

    def run(self) -> int:
        self._ipc.start()
        self._start_listener()
        self._log(
            f"ready (v{__version__}) hotkey={format_hotkey(self._hotkey_keys())} "
            f"listener={self._listener_backend} config={CONFIG_PATH}"
        )
        if self._listener_backend != "evdev":
            self._log(
                "warning: evdev listener unavailable; on Wayland the pynput fallback "
                "cannot see global hotkeys. Run `airtype doctor`."
            )

        try:
            while not self._closed.is_set():
                try:
                    command, reply = self._commands.get(timeout=0.2)
                except queue.Empty:
                    self._restart_listener_after_resume_gap()
                    self._restart_listener_if_dead()
                    continue
                self._handle_command(command, reply)
        finally:
            self.close()
        return 0

    def _submit_command(self, command: dict, reply: Callable[[dict], None]) -> None:
        self._commands.put((command, reply))

    def _handle_command(self, command: dict, reply: Callable[[dict], None] | None) -> None:
        name = command.get("cmd", "")
        result: dict[str, Any]
        if name == "quit":
            result = {"ok": True, "result": "stopping"}
            if reply:
                reply(result)
            self.close()
            return
        if name == "toggle":
            state = self.toggle()
            result = {"ok": True, "result": {"state": state}}
        elif name == "_hotkey_start":
            with self._lock:
                if self._state in {"ready", "unloaded"}:
                    self._start_recording()
            result = {"ok": True}
        elif name == "_hotkey_stop":
            with self._lock:
                if self._state == "recording":
                    self._stop_recording()
            result = {"ok": True}
        elif name == "status":
            result = {"ok": True, "result": self.status()}
        elif name == "reload-config":
            result = {"ok": True, "result": self.reload_config()}
        else:
            result = {"ok": False, "error": f"unknown command: {name}"}
        if reply:
            reply(result)

    # -- status / reload

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "version": __version__,
                "model": self.config["model"],
                "model_loaded": self.pipeline.manager.is_loaded(),
                "listener": self._listener_backend,
                "listener_alive": self._listener_alive(),
                "hotkey": format_hotkey(self._hotkey_keys()),
                "paste_mode": self.config["paste_mode"],
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "config_path": str(CONFIG_PATH),
                "pid": os.getpid(),
            }

    def reload_config(self) -> dict[str, Any]:
        new_config = load_config()
        with self._lock:
            old_config = self.config
            self.config = new_config
            hotkey_changed = (
                new_config["start_hotkey"] != old_config["start_hotkey"]
                or new_config["stop_key"] != old_config["stop_key"]
                or new_config["double_tap_threshold"] != old_config["double_tap_threshold"]
                or new_config["stop_cooldown"] != old_config["stop_cooldown"]
                or new_config["super_release_tolerance"] != old_config["super_release_tolerance"]
            )
            model_changed = new_config["model"] != old_config["model"]

            self.pipeline.paste_mode = new_config["paste_mode"]
            self.pipeline.paste_fallback = new_config["paste_fallback"]
            self.pipeline.terminal_classes = list(new_config["terminal_classes"])
            self.pipeline.sounds_enabled = new_config["sounds_enabled"]

            if hotkey_changed and self._state != "recording":
                with self._policy_lock:
                    self.policy = self._build_policy(new_config)
                self._restart_listener("listener restarted for new hotkey")
            if model_changed and self._state not in {"recording", "transcribing"}:
                # Next borrow reads the new model key from config; drop the old one.
                self.pipeline.manager.unload_now()
                self._state = "ready"
        self._log(
            f"config reloaded: model={new_config['model']} paste={new_config['paste_mode']} "
            f"hotkey={new_config['start_hotkey']} stop={new_config['stop_key']}"
        )
        return self.status()

    # -- listener management

    def _start_listener(self) -> None:
        self._listener, self._listener_backend = start_global_hotkey_listener(
            self._on_press_name,
            self._on_release_name,
            self._hotkey_keys(),
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
        self._restart_listener("listener restarted after death")

    def _restart_listener_after_resume_gap(self) -> None:
        now = time.time()
        gap = now - self._last_loop_at
        self._last_loop_at = now
        if gap < RESUME_GAP_THRESHOLD:
            return
        self._restart_listener("listener refreshed after suspend/resume")

    def _restart_listener(self, message: str) -> None:
        self._stop_listener()
        with self._policy_lock:
            self.policy.reset()
        self._start_listener()
        self._log(f"{message} (backend={self._listener_backend})")
        self._ipc.broadcast(
            {"event": "listener", "listener": self._listener_backend, "message": message}
        )

    def _stop_listener(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    # -- lifecycle

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._stop_listener()
        with self._lock:
            if self._state == "recording":
                self._stop_recording()
        self._ipc.stop()
        self._log("stopped")

    def toggle(self) -> str:
        with self._lock:
            if self._state == "recording":
                self._stop_recording()
            elif self._state in {"ready", "unloaded"}:
                self._start_recording()
            return self._state

    # -- hotkey handling

    def _on_press_name(self, key_name: str) -> None:
        self._on_key(key_name, pressed=True)

    def _on_release_name(self, key_name: str) -> None:
        self._on_key(key_name, pressed=False)

    def _on_key(self, key_name: str, pressed: bool) -> None:
        # Runs on a listener thread: feed the policy and enqueue any action for
        # the main loop so key events keep flowing during transcription.
        if DEBUG:
            self._log(f"key {'press' if pressed else 'release'}: {key_name}")
        with self._policy_lock:
            action = self.policy.feed(key_name, pressed, time.time())
        if action == "start":
            self._commands.put(({"cmd": "_hotkey_start"}, None))
        elif action == "stop":
            self._commands.put(({"cmd": "_hotkey_stop"}, None))

    def _wait_for_modifier_release(self) -> None:
        deadline = time.monotonic() + MODIFIER_RELEASE_WAIT
        while time.monotonic() < deadline:
            with self._policy_lock:
                if self.policy.modifiers_clear():
                    return
            time.sleep(0.02)

    # -- recording

    def _start_recording(self) -> None:
        self._session = self.pipeline.start_recording()
        self._state = "recording"
        with self._policy_lock:
            self.policy.set_recording(True, time.time())
        self._log("recording")
        self._ipc.broadcast({"event": "state", "state": self._state})

    def _stop_recording(self) -> None:
        session = self._session
        if session is None:
            return
        self._session = None
        self._state = "transcribing"
        with self._policy_lock:
            self.policy.set_recording(False, time.time())
        self._log("transcribing")
        self._ipc.broadcast({"event": "state", "state": self._state})
        result = self.pipeline.stop_recording(session)
        self._state = "unloaded" if not self.pipeline.manager.is_loaded() else "ready"
        words = len(result.text.split())
        self._log(
            f"transcript: {words} words in {result.elapsed_seconds:.2f}s "
            f"(copied={result.copied} pasted={result.pasted} backend={result.paste_backend})"
        )
        self._ipc.broadcast(
            {"event": "transcript", "state": self._state, "result": asdict(result)}
        )

    @staticmethod
    def _log(message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}", flush=True)
