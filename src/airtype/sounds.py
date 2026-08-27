import subprocess
import threading
from importlib.resources import as_file, files
from pathlib import Path

_ASSETS = files("airtype.assets")


def _asset_path(name: str) -> Path | None:
    # as_file() handles zip installs; for normal wheel installs this is a real path.
    try:
        with as_file(_ASSETS / name) as path:
            return path if path.exists() else None
    except (FileNotFoundError, OSError):
        return None


SOUND_START = _asset_path("start.mp3")
SOUND_STOP = _asset_path("stop.mp3")


def play_sound(path: Path | None) -> None:
    if path is None or not path.exists():
        return

    commands = (
        ("pw-play", str(path)),
        ("paplay", str(path)),
        ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)),
        ("aplay", str(path)),
    )
    for command in commands:
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except FileNotFoundError:
            continue


def play_sound_later(path: Path | None) -> None:
    thread = threading.Thread(
        target=play_sound,
        args=(path,),
        name=f"airtype-sound-{path.stem if path else 'none'}",
        daemon=True,
    )
    thread.start()
