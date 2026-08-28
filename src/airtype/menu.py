"""Interactive settings menu, gum-based like Omarchy's own terminal menus."""

import shutil
import subprocess
import sys

from . import __version__
from .asr import ensure_model_download, model_exists
from .config import (
    CONCRETE_PASTE_MODES,
    PASTE_MODE_LABELS,
    configured_model_dir,
    load_config,
    update_config,
)
from .hotkey import normalize_hotkey_key_name, parse_start_combo
from .ipc import ServiceNotRunningError, request, service_running
from .registry import MODEL_REGISTRY, get_model_spec
from .systemd import UNIT_PATH, restart_service, service_state

HAVE_GUM = shutil.which("gum") is not None
BACK = "← Back"


def _choose(header: str, options: list[str]) -> str | None:
    if not options:
        return None
    if HAVE_GUM:
        result = subprocess.run(
            ["gum", "choose", "--header", header, *options],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        choice = result.stdout.strip()
        return choice or None
    print(f"\n{header}")
    for index, option in enumerate(options, start=1):
        print(f"  {index}) {option}")
    answer = input("> ").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]
    return None


def _confirm(prompt: str) -> bool:
    if HAVE_GUM:
        return subprocess.run(["gum", "confirm", prompt], check=False).returncode == 0
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _input(prompt: str, value: str = "") -> str:
    if HAVE_GUM:
        result = subprocess.run(
            ["gum", "input", "--header", prompt, "--value", value],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        return result.stdout.strip()
    answer = input(f"{prompt} [{value}]: ").strip()
    return answer or value


def _notify_service_reload() -> None:
    try:
        request("reload-config")
    except ServiceNotRunningError:
        pass


def _status_line() -> str:
    config = load_config()
    spec = get_model_spec(config["model"])
    if service_running():
        try:
            status = request("status")["result"]
            service = f"running ({status['state']}, {status['listener']})"
        except (ServiceNotRunningError, KeyError):
            service = "running"
    else:
        state = service_state()
        service = f"not reachable (systemd: {state})" if UNIT_PATH.exists() else "not installed"
    return (
        f"airtype v{__version__} | service: {service} | model: {spec.display} | "
        f"paste: {PASTE_MODE_LABELS[config['paste_mode']]} | "
        f"hotkey: {config['start_hotkey']} (stop: {config['stop_key']})"
    )


def run_menu() -> int:
    if not sys.stdin.isatty():
        print("airtype: the settings menu needs a terminal", file=sys.stderr)
        return 1

    while True:
        print()
        print(_status_line())
        choice = _choose(
            "AirType settings",
            [
                "Toggle recording",
                "Model",
                "Paste mode",
                "Hotkey",
                "Terminal classes",
                "Sounds",
                "Waveform overlay",
                "Restart service",
                "Doctor",
                "Quit",
            ],
        )
        if choice is None or choice == "Quit":
            return 0
        if choice == "Toggle recording":
            _menu_toggle()
        elif choice == "Model":
            _menu_model()
        elif choice == "Paste mode":
            _menu_paste()
        elif choice == "Hotkey":
            _menu_hotkey()
        elif choice == "Terminal classes":
            _menu_terminal_classes()
        elif choice == "Sounds":
            _menu_sounds()
        elif choice == "Waveform overlay":
            _menu_overlay()
        elif choice == "Restart service":
            print("restarted" if restart_service() else "restart failed (is it installed?)")
        elif choice == "Doctor":
            from .cli import run_doctor

            run_doctor(fix=False)


def _menu_toggle() -> None:
    try:
        response = request("toggle")
        print(f"service state: {response['result']['state']}")
    except ServiceNotRunningError as exc:
        print(exc)


def _menu_model() -> None:
    config = load_config()
    active = config["model"]
    options = []
    labels = {}
    for spec in MODEL_REGISTRY.values():
        model_dir = configured_model_dir(spec.dir_name)
        markers = []
        if spec.name == active:
            markers.append("active")
        if model_exists(spec, model_dir):
            markers.append("downloaded")
        suffix = f" [{', '.join(markers)}]" if markers else ""
        label = f"{spec.display} - {spec.langs}{suffix}"
        options.append(label)
        labels[label] = spec.name
    options.append("Delete a downloaded model")
    options.append(BACK)

    choice = _choose("Model (select to activate; downloads if missing)", options)
    if choice is None or choice == BACK:
        return
    if choice == "Delete a downloaded model":
        _menu_model_delete()
        return

    spec = get_model_spec(labels[choice])
    model_dir = configured_model_dir(spec.dir_name)
    if not model_exists(spec, model_dir):
        if not _confirm(f"Download {spec.display} ({spec.size_label})?"):
            return
        print(f"Downloading {spec.dir_name} ...")
        ensure_model_download(spec, model_dir, progress=True)
        print(f"Model ready: {model_dir}")
    update_config(model=spec.name, model_download_approved=True)
    _notify_service_reload()
    print(f"Active model: {spec.display}")


def _menu_model_delete() -> None:
    config = load_config()
    options = []
    labels = {}
    for spec in MODEL_REGISTRY.values():
        model_dir = configured_model_dir(spec.dir_name)
        if model_exists(spec, model_dir):
            size_mb = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file()) // (1 << 20)
            note = " (active - will re-download on next use)" if spec.name == config["model"] else ""
            label = f"{spec.display} - {size_mb} MB on disk{note}"
            options.append(label)
            labels[label] = spec
    if not options:
        print("No downloaded models to delete.")
        return
    options.append(BACK)
    choice = _choose("Delete which model?", options)
    if choice is None or choice == BACK:
        return
    spec = labels[choice]
    model_dir = configured_model_dir(spec.dir_name)
    if _confirm(f"Delete {model_dir}?"):
        shutil.rmtree(model_dir, ignore_errors=True)
        print(f"Deleted {model_dir}")


def _menu_paste() -> None:
    labels = {PASTE_MODE_LABELS[mode]: mode for mode in PASTE_MODE_LABELS if mode not in {"cmd_v", "cmd_shift_v"}}
    choice = _choose("Paste mode", [*labels, BACK])
    if choice is None or choice == BACK:
        return
    mode = labels[choice]
    changes = {"paste_mode": mode}
    if mode == "auto":
        fallback_labels = {
            PASTE_MODE_LABELS[m]: m for m in CONCRETE_PASTE_MODES if m in {"ctrl_v", "ctrl_shift_v", "copy_only"}
        }
        fallback = _choose("Fallback when the window class is unknown", list(fallback_labels))
        if fallback:
            changes["paste_fallback"] = fallback_labels[fallback]
    update_config(**changes)
    _notify_service_reload()
    print(f"Paste mode: {PASTE_MODE_LABELS[mode]}")


def _menu_hotkey() -> None:
    config = load_config()
    start = _input(
        "Start hotkey (modifiers+tap key, double-tap the last key)", config["start_hotkey"]
    )
    stop = _input("Stop key (single tap while recording)", config["stop_key"])
    modifiers, tap_key = parse_start_combo(start)
    stop_key = normalize_hotkey_key_name(stop) or tap_key
    normalized_start = "+".join([*sorted(modifiers), tap_key])
    update_config(start_hotkey=normalized_start, stop_key=stop_key)
    _notify_service_reload()
    print(f"Hotkey: hold {'+'.join(sorted(modifiers)) or '(none)'} and double-tap {tap_key}; tap {stop_key} to stop")


def _menu_terminal_classes() -> None:
    while True:
        config = load_config()
        classes = config["terminal_classes"]
        print(f"\nTerminal classes ({len(classes)}): {', '.join(classes)}")
        choice = _choose("Terminal classes (used by Auto paste)", ["Add", "Remove", BACK])
        if choice is None or choice == BACK:
            return
        if choice == "Add":
            new_class = _input("Window class to treat as a terminal (see: hyprctl activewindow)").strip().lower()
            if new_class and new_class not in classes:
                update_config(terminal_classes=[*classes, new_class])
                _notify_service_reload()
        elif choice == "Remove":
            target = _choose("Remove which class?", [*classes, BACK])
            if target and target != BACK:
                update_config(terminal_classes=[cls for cls in classes if cls != target])
                _notify_service_reload()


def _menu_sounds() -> None:
    config = load_config()
    enabled = not config["sounds_enabled"]
    update_config(sounds_enabled=enabled)
    _notify_service_reload()
    print(f"Sounds: {'on' if enabled else 'off'}")


def _menu_overlay() -> None:
    config = load_config()
    enabled = not config["overlay_enabled"]
    update_config(overlay_enabled=enabled)
    _notify_service_reload()
    print(f"Waveform overlay: {'on' if enabled else 'off'}")
