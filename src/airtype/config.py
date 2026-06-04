import json
import os
import platform
from pathlib import Path
from typing import Any

from platformdirs import PlatformDirs

APP_NAME = "airtype"
MODEL_SIZE_LABEL = "about 600 MB"
PASTE_MODE_LABELS = {
    "copy_only": "Copy only",
    "ctrl_v": "Ctrl+V",
    "ctrl_shift_v": "Ctrl+Shift+V",
    "cmd_v": "Command+V",
    "cmd_shift_v": "Command+Shift+V",
}
PASTE_MODE_CYCLE = ("ctrl_shift_v", "ctrl_v", "cmd_v", "cmd_shift_v", "copy_only")
HOTKEY_CYCLE = ("alt", "ctrl", "shift", "capslock", "super")

DIRS = PlatformDirs(APP_NAME, appauthor=False, ensure_exists=True)
CONFIG_PATH = DIRS.user_config_path / "config.json"


def default_paste_mode() -> str:
    if platform.system() == "Darwin":
        return "cmd_v"
    return "ctrl_shift_v"


def default_config() -> dict[str, Any]:
    return {
        "model_dir": None,
        "paste_mode": default_paste_mode(),
        "hotkey": "alt",
        "model_download_approved": False,
    }


def load_config() -> dict[str, Any]:
    config = default_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return config
    if isinstance(data, dict):
        config.update({key: value for key, value in data.items() if key in config})
    config["paste_mode"] = normalize_paste_mode(str(config.get("paste_mode") or ""))
    config["hotkey"] = normalize_hotkey(str(config.get("hotkey") or ""))
    return config


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def update_config(**changes: Any) -> dict[str, Any]:
    config = load_config()
    config.update(changes)
    config["paste_mode"] = normalize_paste_mode(str(config.get("paste_mode") or ""))
    config["hotkey"] = normalize_hotkey(str(config.get("hotkey") or ""))
    save_config(config)
    return config


def normalize_paste_mode(mode: str) -> str:
    normalized = mode.strip().lower().replace("-", "_")
    aliases = {
        "copy": "copy_only",
        "control_v": "ctrl_v",
        "control_shift_v": "ctrl_shift_v",
        "command_v": "cmd_v",
        "command_shift_v": "cmd_shift_v",
        "cmd": "cmd_v",
        "ctrl": "ctrl_v",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in PASTE_MODE_LABELS:
        return default_paste_mode()
    return normalized


def normalize_hotkey(hotkey: str) -> str:
    normalized = hotkey.strip().lower().replace("-", "_")
    aliases = {
        "option": "alt",
        "control": "ctrl",
        "command": "cmd",
        "cmd": "super" if platform.system() != "Darwin" else "cmd",
        "meta": "super",
        "win": "super",
        "windows": "super",
        "caps_lock": "capslock",
    }
    normalized = aliases.get(normalized, normalized)
    if platform.system() == "Darwin" and normalized == "super":
        normalized = "cmd"
    allowed = set(HOTKEY_CYCLE) | {"cmd"}
    return normalized if normalized in allowed else "alt"


def cycle_value(current: str, values: tuple[str, ...]) -> str:
    try:
        index = values.index(current)
    except ValueError:
        return values[0]
    return values[(index + 1) % len(values)]


def cycle_paste_mode() -> dict[str, Any]:
    config = load_config()
    config["paste_mode"] = cycle_value(config["paste_mode"], PASTE_MODE_CYCLE)
    save_config(config)
    return config


def cycle_hotkey() -> dict[str, Any]:
    config = load_config()
    cycle = ("cmd", "alt", "ctrl", "shift", "capslock") if platform.system() == "Darwin" else HOTKEY_CYCLE
    config["hotkey"] = cycle_value(config["hotkey"], cycle)
    save_config(config)
    return config


def model_base_dir() -> Path:
    return DIRS.user_cache_path / "models"


def configured_model_dir(model_name: str) -> Path:
    env_model_dir = os.environ.get("AIRTYPE_MODEL_DIR")
    if env_model_dir:
        return Path(env_model_dir).expanduser()

    config = load_config()
    configured = config.get("model_dir")
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser()
    return model_base_dir() / model_name


def resolve_custom_model_dir(value: str, model_name: str) -> Path:
    path = Path(value).expanduser()
    if path.name == model_name:
        return path
    return path / model_name


def public_settings(model_name: str) -> dict[str, Any]:
    config = load_config()
    model_dir = configured_model_dir(model_name)
    return {
        "config_path": str(CONFIG_PATH),
        "cache_dir": str(DIRS.user_cache_path),
        "model_dir": str(model_dir),
        "model_name": model_name,
        "model_size": MODEL_SIZE_LABEL,
        "model_exists": all(
            (model_dir / name).is_file()
            for name in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
        ),
        "paste_mode": config["paste_mode"],
        "paste_label": PASTE_MODE_LABELS[config["paste_mode"]],
        "hotkey": config["hotkey"],
        "model_download_approved": bool(config.get("model_download_approved")),
    }
