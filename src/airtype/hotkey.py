import threading
from pathlib import Path

DEFAULT_HOTKEY_KEYS = ("alt",)
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
        return list(DEFAULT_HOTKEY_KEYS)

    keys = []
    seen = set()
    for raw_key in value.replace("+", ",").split(","):
        key = normalize_hotkey_key_name(raw_key)
        if key and key not in seen:
            keys.append(key)
            seen.add(key)
    return keys or list(DEFAULT_HOTKEY_KEYS)


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
    def __init__(self, on_press, on_release, hotkey_keys: set[str]) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._hotkey_keys = hotkey_keys
        self._stop = threading.Event()
        self._devices = []
        self._threads: list[threading.Thread] = []

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
                    key_name = key_names.get(event.code)
                    if key_name is None:
                        continue
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
