"""Detect the focused window so paste can be terminal-aware on Hyprland."""

import json
import shutil
import subprocess


def active_window_class() -> str | None:
    """Return the focused window's class via hyprctl, or None off-Hyprland."""
    if shutil.which("hyprctl") is None:
        return None
    try:
        result = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    window_class = data.get("class")
    if isinstance(window_class, str) and window_class:
        return window_class
    return None


def is_terminal_class(window_class: str | None, terminal_classes: list[str]) -> bool:
    if not window_class:
        return False
    return window_class.strip().lower() in {cls.lower() for cls in terminal_classes}


def resolve_paste_mode(
    paste_mode: str,
    paste_fallback: str,
    terminal_classes: list[str],
) -> str:
    """Resolve "auto" at paste time: terminals get Ctrl+Shift+V, others Ctrl+V."""
    if paste_mode != "auto":
        return paste_mode

    window_class = active_window_class()
    if window_class is None:
        return paste_fallback
    if is_terminal_class(window_class, terminal_classes):
        return "ctrl_shift_v"
    return "ctrl_v"
