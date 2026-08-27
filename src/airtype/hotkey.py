import threading
from pathlib import Path

DEFAULT_START_HOTKEY = "super+alt"
DEFAULT_STOP_KEY = "alt"
EVDEV_KEY_NAMES = {
    "KEY_LEFTALT": "alt",
    "KEY_RIGHTALT": "alt",
    "KEY_LEFTCTRL": "ctrl",
    "KEY_RIGHTCTRL": "ctrl",
    "KEY_LEFTSHIFT": "shift",
    "KEY_RIGHTSHIFT": "shift",
    "KEY_LEFTMETA": "super",
    "KEY_RIGHTMETA": "super",
    "KEY_CAPSLOCK": "capslock",
    "KEY_PAGEUP": "pageup",
    "KEY_PAGEDOWN": "pagedown",
}


def normalize_hotkey_key_name(name: str) -> str:
    lowered = name.strip().lower()
    aliases = {
        "alt_l": "alt",
        "alt_r": "alt",
        "option": "alt",
        "ctrl_l": "ctrl",
        "ctrl_r": "ctrl",
        "control": "ctrl",
        "control_l": "ctrl",
        "control_r": "ctrl",
        "command": "cmd",
        "cmd_l": "cmd",
        "cmd_r": "cmd",
        "meta": "super",
        "win": "super",
        "windows": "super",
        "shift_l": "shift",
        "shift_r": "shift",
        "caps_lock": "capslock",
        "caps-lock": "capslock",
        "page_up": "pageup",
        "page_down": "pagedown",
    }
    return aliases.get(lowered, lowered)


def parse_hotkey_keys(value: str | None) -> list[str]:
    if not value:
        return ["alt"]

    keys = []
    seen = set()
    for raw_key in value.replace("+", ",").split(","):
        key = normalize_hotkey_key_name(raw_key)
        if key and key not in seen:
            keys.append(key)
            seen.add(key)
    return keys or ["alt"]


def parse_start_combo(value: str | None) -> tuple[set[str], str]:
    """Split "super+alt" into (modifiers, tap key). The last key is the tap key."""
    keys = parse_hotkey_keys(value or DEFAULT_START_HOTKEY)
    return set(keys[:-1]), keys[-1]


def format_hotkey(keys: list[str]) -> str:
    order = {"ctrl": 0, "shift": 1, "alt": 2, "cmd": 3, "super": 4}
    labels = {
        "ctrl": "Ctrl",
        "shift": "Shift",
        "alt": "Alt",
        "cmd": "Command",
        "super": "Super",
        "capslock": "CapsLock",
        "pageup": "PageUp",
        "pagedown": "PageDown",
    }
    return "+".join(
        labels.get(key, key.title())
        for key in sorted(keys, key=lambda key: (order.get(key, 99), key))
    )


class HotkeyPolicy:
    """Pure hotkey state machine: hold-modifiers + double-tap starts, clean tap stops.

    Fed (key_name, pressed, timestamp) events; returns "start", "stop", or None.
    The service owns the recording state and reports it via set_recording().
    """

    def __init__(
        self,
        start_modifiers: set[str],
        tap_key: str,
        stop_key: str,
        double_tap_threshold: float = 0.3,
        stop_cooldown: float = 0.25,
        modifier_release_tolerance: float = 0.5,
    ) -> None:
        self.start_modifiers = set(start_modifiers)
        self.tap_key = tap_key
        self.stop_key = stop_key
        self.double_tap_threshold = double_tap_threshold
        self.stop_cooldown = stop_cooldown
        self.modifier_release_tolerance = modifier_release_tolerance
        self.pressed: set[str] = set()
        self._modifier_release_ts: dict[str, float] = {}
        self._last_tap_ts = 0.0
        self._stop_tap_armed = False
        self._stop_tap_dirty = False
        self._recording = False
        self._record_start_ts = 0.0

    def set_recording(self, recording: bool, now: float) -> None:
        self._recording = recording
        if recording:
            self._record_start_ts = now
        self._stop_tap_armed = False
        self._last_tap_ts = 0.0

    def reset(self) -> None:
        self.pressed.clear()
        self._modifier_release_ts.clear()
        self._last_tap_ts = 0.0
        self._stop_tap_armed = False

    def modifiers_clear(self) -> bool:
        return not self.pressed.intersection({"ctrl", "shift", "alt", "super", "cmd"})

    def _modifiers_effectively_held(self, now: float) -> bool:
        for modifier in self.start_modifiers:
            if modifier in self.pressed:
                continue
            released_at = self._modifier_release_ts.get(modifier, 0.0)
            if now - released_at >= self.modifier_release_tolerance:
                return False
        return True

    def feed(self, key_name: str, pressed: bool, now: float) -> str | None:
        if pressed:
            return self._feed_press(key_name, now)
        return self._feed_release(key_name, now)

    def _feed_press(self, key_name: str, now: float) -> str | None:
        # Any other key pressed while a stop tap is held makes it a chord
        # (e.g. Alt+Tab), not a stop request.
        if self._stop_tap_armed and key_name != self.stop_key:
            self._stop_tap_dirty = True
        self.pressed.add(key_name)

        if self._recording:
            if key_name == self.stop_key:
                self._stop_tap_armed = True
                self._stop_tap_dirty = False
            return None

        if key_name == self.tap_key:
            if not self._modifiers_effectively_held(now):
                self._last_tap_ts = 0.0
                return None
            if now - self._last_tap_ts < self.double_tap_threshold:
                self._last_tap_ts = 0.0
                return "start"
            self._last_tap_ts = now
        return None

    def _feed_release(self, key_name: str, now: float) -> str | None:
        self.pressed.discard(key_name)
        if key_name in self.start_modifiers:
            self._modifier_release_ts[key_name] = now

        if (
            self._recording
            and key_name == self.stop_key
            and self._stop_tap_armed
        ):
            self._stop_tap_armed = False
            if not self._stop_tap_dirty and now - self._record_start_ts > self.stop_cooldown:
                return "stop"
        return None


def pynput_key_to_name(key) -> str | None:
    char = getattr(key, "char", None)
    if char:
        return normalize_hotkey_key_name(char)

    key_name = getattr(key, "name", None)
    if key_name:
        return normalize_hotkey_key_name(key_name)

    value = str(key)
    if value.startswith("Key."):
        return normalize_hotkey_key_name(value.removeprefix("Key."))
    return None


class EvdevHotkeyListener:
    """Reads /dev/input key events below the compositor. Reports ALL keys.

    Devices are selected by the hotkey key codes, but every key event on those
    devices is forwarded so HotkeyPolicy can detect chords like Alt+Tab.
    """

    def __init__(self, on_press, on_release, hotkey_keys: set[str]) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._hotkey_keys = hotkey_keys
        self._stop = threading.Event()
        self._devices = []
        self._threads: list[threading.Thread] = []
        self.device_paths: set[str] = set()

    @classmethod
    def start(cls, on_press, on_release, hotkey_keys: set[str]):
        try:
            from evdev import InputDevice, ecodes, list_devices
        except ImportError:
            return None

        listener = cls(on_press, on_release, hotkey_keys)
        needed_codes = {
            code
            for code, key_name in cls._key_code_names(ecodes).items()
            if key_name in hotkey_keys
        }
        if not needed_codes:
            return None

        for path in list_devices():
            try:
                device = InputDevice(path)
                key_caps = set(device.capabilities().get(ecodes.EV_KEY, []))
            except (OSError, PermissionError):
                continue
            if key_caps.intersection(needed_codes):
                listener._devices.append(device)
                listener.device_paths.add(path)

        if not listener._devices:
            return None

        for device in listener._devices:
            thread = threading.Thread(
                target=listener._run_device,
                args=(device,),
                name=f"airtype-hotkey-{Path(device.path).name}",
                daemon=True,
            )
            listener._threads.append(thread)
            thread.start()
        return listener

    @staticmethod
    def _key_code_names(ecodes) -> dict[int, str]:
        names = {}
        for code_name, key_name in EVDEV_KEY_NAMES.items():
            code = getattr(ecodes, code_name, None)
            if code is not None:
                names[code] = key_name
        return names

    @staticmethod
    def _event_key_name(ecodes, code: int, known: dict[int, str]) -> str:
        name = known.get(code)
        if name is not None:
            return name
        raw = ecodes.KEY.get(code)
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        if isinstance(raw, str):
            return raw.removeprefix("KEY_").lower()
        return f"code_{code}"

    def _run_device(self, device) -> None:
        import select

        from evdev import ecodes

        key_names = self._key_code_names(ecodes)
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([device], [], [], 0.2)
                if not readable:
                    continue
                for event in device.read():
                    if event.type != ecodes.EV_KEY or event.value not in {0, 1}:
                        continue
                    key_name = self._event_key_name(ecodes, event.code, key_names)
                    if event.value == 1:
                        self._on_press(key_name)
                    else:
                        self._on_release(key_name)
            except OSError:
                break

    def stop(self) -> None:
        self._stop.set()
        for device in self._devices:
            try:
                device.close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(timeout=0.5)

    def is_alive(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)


def matching_device_paths(hotkey_keys: set[str]) -> set[str]:
    """Paths of input devices that carry the hotkey key codes right now.

    Used by the service's hotplug check to notice keyboards that appear
    after the listener enumerated devices (USB/2.4G dongles, Bluetooth).
    """
    try:
        from evdev import InputDevice, ecodes, list_devices
    except ImportError:
        return set()

    needed_codes = {
        code
        for code, key_name in EvdevHotkeyListener._key_code_names(ecodes).items()
        if key_name in hotkey_keys
    }
    paths: set[str] = set()
    for path in list_devices():
        try:
            device = InputDevice(path)
            key_caps = set(device.capabilities().get(ecodes.EV_KEY, []))
            device.close()
        except (OSError, PermissionError):
            continue
        if key_caps.intersection(needed_codes):
            paths.add(path)
    return paths


def start_global_hotkey_listener(on_press_name, on_release_name, hotkey_keys: list[str]):
    listener = EvdevHotkeyListener.start(
        on_press_name,
        on_release_name,
        set(hotkey_keys),
    )
    if listener is not None:
        return listener, "evdev"

    from pynput import keyboard as pynput_keyboard

    def on_press(key):
        key_name = pynput_key_to_name(key)
        if key_name is not None:
            on_press_name(key_name)

    def on_release(key):
        key_name = pynput_key_to_name(key)
        if key_name is not None:
            on_release_name(key_name)

    listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    return listener, "pynput"
