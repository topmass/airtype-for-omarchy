"""Install/uninstall the airtype systemd user service."""

import shutil
import subprocess
from pathlib import Path

UNIT_NAME = "airtype.service"
UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / UNIT_NAME

UNIT_TEMPLATE = """\
[Unit]
Description=AirType push-to-talk dictation service
Documentation=https://github.com/topmass/AirType
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart={exec_path} service
Restart=on-failure
RestartSec=5
Environment=XDG_RUNTIME_DIR=%t

[Install]
WantedBy=graphical-session.target
"""


def resolve_exec_path() -> str:
    found = shutil.which("airtype")
    if found:
        return found
    return str(Path.home() / ".local" / "bin" / "airtype")


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def install_service() -> str:
    exec_path = resolve_exec_path()
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(UNIT_TEMPLATE.format(exec_path=exec_path), encoding="utf-8")
    _systemctl("daemon-reload")
    result = _systemctl("enable", "--now", UNIT_NAME)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "systemctl enable failed")
    return str(UNIT_PATH)


def uninstall_service() -> None:
    _systemctl("disable", "--now", UNIT_NAME)
    UNIT_PATH.unlink(missing_ok=True)
    _systemctl("daemon-reload")


def service_state() -> str:
    result = _systemctl("is-active", UNIT_NAME)
    return result.stdout.strip() or "unknown"


def restart_service() -> bool:
    return _systemctl("restart", UNIT_NAME).returncode == 0
