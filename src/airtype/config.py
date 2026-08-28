import json
import os
import platform
from pathlib import Path
from typing import Any

from platformdirs import PlatformDirs

from .registry import DEFAULT_MODEL_KEY, MODEL_REGISTRY, get_model_spec

APP_NAME = "airtype"
CONFIG_VERSION = 2
PASTE_MODE_LABELS = {
    "auto": "Auto (terminal-aware)",
    "copy_only": "Copy only",
    "ctrl_v": "Ctrl+V",
    "ctrl_shift_v": "Ctrl+Shift+V",
    "cmd_v": "Command+V",
    "cmd_shift_v": "Command+Shift+V",
}
# "auto" resolves at paste time; these are the concrete modes it can resolve to.
CONCRETE_PASTE_MODES = ("ctrl_v", "ctrl_shift_v", "cmd_v", "cmd_shift_v", "copy_only")

# Window classes treated as terminals by the "auto" paste mode (lowercase).
DEFAULT_TERMINAL_CLASSES = [
    "foot",
    "footclient",
    "alacritty",
    "kitty",
    "com.mitchellh.ghostty",
    "ghostty",
    "org.wezfurlong.wezterm",
    "wezterm",
    "konsole",
    "org.kde.konsole",
    "xterm",
    "st",
    "urxvt",
    "rxvt",
    "terminator",
    "org.gnome.terminal",
    "gnome-terminal-server",
    "tilix",
    "xfce4-terminal",
    "io.elementary.terminal",
    "warp",
    "dev.warp.warp",
    "rio",
    "contour",
    "zutty",
    "deepin-terminal",
    "lxterminal",
    "qterminal",
    "sakura",
    "guake",
    "wave",
]

DIRS = PlatformDirs(APP_NAME, appauthor=False, ensure_exists=True)
CONFIG_PATH = DIRS.user_config_path / "config.json"


def default_paste_mode() -> str:
    if platform.system() == "Darwin":
        return "cmd_v"
    return "auto"


def default_config() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "model": DEFAULT_MODEL_KEY,
        "model_dir": None,
        "paste_mode": default_paste_mode(),
        "paste_fallback": "ctrl_v",
        "start_hotkey": "super+alt",
        "stop_key": "alt",
        "double_tap_threshold": 0.3,
        "stop_cooldown": 0.25,
        "super_release_tolerance": 0.5,
        "terminal_classes": list(DEFAULT_TERMINAL_CLASSES),
        "sounds_enabled": True,
        "overlay_enabled": True,
        "model_download_approved": False,
    }


def migrate_config(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a raw config dict to the current schema. Safe on partial input."""
    config = default_config()
    if not isinstance(data, dict):
        return config

    if data.get("version") == CONFIG_VERSION:
        config.update({key: value for key, value in data.items() if key in config})
    else:
        # v1 schema: {model_dir, paste_mode, hotkey, model_download_approved}
        for key in ("model_dir", "model_download_approved"):
            if key in data:
                config[key] = data[key]
        if data.get("paste_mode"):
            config["paste_mode"] = str(data["paste_mode"])
        # v1 model_dir pointed at the model folder itself; v2 stores the parent.
        known_dir_names = {spec.dir_name for spec in MODEL_REGISTRY.values()}
        if isinstance(config.get("model_dir"), str):
            path = Path(config["model_dir"]).expanduser()
            if path.name in known_dir_names:
                config["model_dir"] = str(path.parent)
        old_hotkey = str(data.get("hotkey") or "").strip()
        if old_hotkey:
            config["start_hotkey"] = (
                old_hotkey if "+" in old_hotkey else f"super+{old_hotkey}"
            )
            config["stop_key"] = old_hotkey.replace("+", ",").split(",")[-1].strip()

    config["paste_mode"] = normalize_paste_mode(str(config.get("paste_mode") or ""))
    config["paste_fallback"] = normalize_concrete_paste_mode(
        str(config.get("paste_fallback") or "")
    )
    if config.get("model") not in MODEL_REGISTRY:
        config["model"] = DEFAULT_MODEL_KEY
    if not isinstance(config.get("terminal_classes"), list):
        config["terminal_classes"] = list(DEFAULT_TERMINAL_CLASSES)
    config["terminal_classes"] = [
        str(cls).strip().lower() for cls in config["terminal_classes"] if str(cls).strip()
    ]
    for key, minimum in (
        ("double_tap_threshold", 0.1),
        ("stop_cooldown", 0.0),
        ("super_release_tolerance", 0.0),
    ):
        try:
            config[key] = max(minimum, float(config[key]))
        except (TypeError, ValueError):
            config[key] = default_config()[key]
    config["sounds_enabled"] = bool(config.get("sounds_enabled", True))
    config["overlay_enabled"] = bool(config.get("overlay_enabled", True))
    return config


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default_config()
    migrated = migrate_config(data)
    if isinstance(data, dict) and data.get("version") != CONFIG_VERSION:
        try:
            save_config(migrated)
        except OSError:
            pass
    return migrated


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def update_config(**changes: Any) -> dict[str, Any]:
    config = load_config()
    config.update(changes)
    config = migrate_config(config)
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
        "terminal_aware": "auto",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in PASTE_MODE_LABELS:
        return default_paste_mode()
    return normalized


def normalize_concrete_paste_mode(mode: str) -> str:
    normalized = normalize_paste_mode(mode)
    if normalized not in CONCRETE_PASTE_MODES:
        return "ctrl_v"
    return normalized


def model_base_dir() -> Path:
    return DIRS.user_cache_path / "models"


def configured_model_dir(dir_name: str) -> Path:
    env_model_dir = os.environ.get("AIRTYPE_MODEL_DIR")
    if env_model_dir:
        return Path(env_model_dir).expanduser()

    config = load_config()
    configured = config.get("model_dir")
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser() / dir_name
    return model_base_dir() / dir_name


def resolve_custom_model_base(value: str) -> Path:
    """A custom model_dir is a parent directory that holds one dir per model."""
    return Path(value).expanduser()


def public_settings() -> dict[str, Any]:
    from .asr import model_exists

    config = load_config()
    spec = get_model_spec(config["model"])
    model_dir = configured_model_dir(spec.dir_name)
    return {
        "config_path": str(CONFIG_PATH),
        "cache_dir": str(DIRS.user_cache_path),
        "model": spec.name,
        "model_dir": str(model_dir),
        "model_display": spec.display,
        "model_size": spec.size_label,
        "model_exists": model_exists(spec, model_dir),
        "paste_mode": config["paste_mode"],
        "paste_label": PASTE_MODE_LABELS[config["paste_mode"]],
        "paste_fallback": config["paste_fallback"],
        "start_hotkey": config["start_hotkey"],
        "stop_key": config["stop_key"],
        "sounds_enabled": config["sounds_enabled"],
        "overlay_enabled": config["overlay_enabled"],
        "model_download_approved": bool(config.get("model_download_approved")),
    }
