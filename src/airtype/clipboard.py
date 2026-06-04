import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from .config import normalize_paste_mode

YDOTOOL_SOCKET_CANDIDATES = (
    lambda: Path(os.environ["YDOTOOL_SOCKET"]) if os.environ.get("YDOTOOL_SOCKET") else None,
    lambda: Path(os.environ["XDG_RUNTIME_DIR"]) / ".ydotool_socket"
    if os.environ.get("XDG_RUNTIME_DIR")
    else None,
    lambda: Path(f"/run/user/{os.getuid()}/.ydotool_socket"),
    lambda: Path("/tmp/.ydotool_socket"),
)


def copy_to_clipboard(text: str) -> bool:
    system = platform.system()
    if system == "Darwin":
        commands = [["pbcopy"]]
    elif system == "Windows":
        commands = [["clip"]]
    elif (os.environ.get("XDG_SESSION_TYPE") or "").lower() == "wayland":
        commands = [["wl-copy"]]
    else:
        commands = [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]

    for command in commands:
        try:
            subprocess.run(command, input=text.encode(), check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        pass
    return False


def auto_paste(mode: str) -> tuple[bool, str]:
    paste_mode = normalize_paste_mode(mode)
    if paste_mode == "copy_only":
        return False, "copy_only"

    system = platform.system()
    if system in {"Darwin", "Windows"}:
        if _paste_with_pynput(paste_mode):
            return True, "pynput"
        return False, "unavailable"

    session_type = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()

    if session_type == "wayland":
        if "KDE" not in desktop and _paste_with_wtype(paste_mode):
            return True, "wtype"
        if _paste_with_ydotool(paste_mode):
            return True, "ydotool"
        return False, "unavailable"

    if _paste_with_xdotool(paste_mode):
        return True, "xdotool"
    if _paste_with_pynput(paste_mode):
        return True, "pynput"
    return False, "unavailable"


def describe_auto_paste_backend(mode: str) -> str:
    paste_mode = normalize_paste_mode(mode)
    if paste_mode == "copy_only":
        return "Copy only"

    system = platform.system()
    if system == "Darwin":
        return "macOS paste uses Command key via accessibility permissions"
    if system == "Windows":
        return "Windows paste uses Ctrl key via pynput"

    session_type = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    if session_type == "wayland":
        socket_path = get_ydotool_socket_path()
        if socket_path is not None:
            return f"Wayland backend ready via ydotool ({socket_path})"
        if shutil.which("wtype") is not None:
            return "Wayland backend ready via wtype"
        if shutil.which("ydotool") is None:
            return "Wayland auto-paste needs ydotool"
        return "ydotoold socket not found or inaccessible"

    if shutil.which("xdotool") is not None:
        return "X11 backend ready via xdotool"
    return "Auto-paste backend unavailable"


def get_ydotool_socket_path() -> Path | None:
    for candidate_factory in YDOTOOL_SOCKET_CANDIDATES:
        candidate = candidate_factory()
        if candidate and candidate.exists() and os.access(candidate, os.R_OK | os.W_OK):
            return candidate
    return None


def _paste_with_ydotool(mode: str) -> bool:
    if shutil.which("ydotool") is None:
        return False

    socket_path = get_ydotool_socket_path()
    if socket_path is None:
        return False

    if mode == "ctrl_shift_v":
        key_sequence = ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]
    else:
        key_sequence = ["29:1", "47:1", "47:0", "29:0"]

    result = subprocess.run(
        ["ydotool", "key", *key_sequence],
        env={**os.environ, "YDOTOOL_SOCKET": str(socket_path)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _paste_with_wtype(mode: str) -> bool:
    if shutil.which("wtype") is None:
        return False

    if mode == "ctrl_shift_v":
        args = ["wtype", "-M", "ctrl", "-M", "shift", "-k", "v"]
    else:
        args = ["wtype", "-M", "ctrl", "-k", "v"]

    result = subprocess.run(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _paste_with_xdotool(mode: str) -> bool:
    if shutil.which("xdotool") is None:
        return False

    chord = "ctrl+shift+v" if mode == "ctrl_shift_v" else "ctrl+v"
    time.sleep(0.05)
    result = subprocess.run(
        ["xdotool", "key", "--clearmodifiers", chord],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _paste_with_pynput(mode: str) -> bool:
    try:
        from pynput.keyboard import Controller, Key
    except ImportError:
        return False

    paste_mode = normalize_paste_mode(mode)
    if paste_mode in {"cmd_v", "cmd_shift_v"}:
        modifier = Key.cmd
    else:
        modifier = Key.ctrl

    combo: list[object] = [modifier]
    if paste_mode in {"ctrl_shift_v", "cmd_shift_v"}:
        combo.append(Key.shift)
    combo.append("v")

    keyboard = Controller()
    try:
        time.sleep(0.05)
        for key in combo[:-1]:
            keyboard.press(key)
        keyboard.press(combo[-1])
        keyboard.release(combo[-1])
        for key in reversed(combo[:-1]):
            keyboard.release(key)
        return True
    except Exception:
        return False
