import subprocess
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2]
SOUND_START = APP_DIR / "soundfx" / "start.mp3"
SOUND_STOP = APP_DIR / "soundfx" / "stop.mp3"


def play_sound(path: Path) -> None:
    if not path.exists():
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


def play_sound_later(path: Path) -> None:
    thread = threading.Thread(
        target=play_sound,
        args=(path,),
        name=f"airtype-sound-{path.stem}",
        daemon=True,
    )
    thread.start()
