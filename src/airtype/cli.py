import argparse
import grp
import json
import os
import platform
import shutil
import sys
from pathlib import Path

from . import __version__
from .asr import default_model_dir, ensure_model_download, model_exists
from .clipboard import describe_auto_paste_backend
from .config import (
    load_config,
    normalize_paste_mode,
    public_settings,
    resolve_custom_model_base,
    update_config,
)
from .ipc import ServiceNotRunningError, request, socket_path
from .pipeline import AirtypePipeline
from .registry import MODEL_REGISTRY, get_model_spec
from .service import AirtypeService
from .terminal import active_window_class


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        from .menu import run_menu

        return run_menu()

    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command

    try:
        if command == "service":
            return _service(args)
        if command == "toggle":
            return _toggle(args)
        if command == "status":
            return _status(args)
        if command == "record":
            if not _ensure_model_ready(interactive=sys.stdin.isatty()):
                return 1
            return _record(args)
        if command == "transcribe":
            return _transcribe(args)
        if command == "setup":
            return _setup(args)
        if command == "models":
            return _models(args)
        if command == "settings":
            return _settings(args)
        if command == "doctor":
            return run_doctor(fix=args.fix)
        if command == "install":
            return _install(args)
        if command == "uninstall":
            return _uninstall(args)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except ServiceNotRunningError as exc:
        print(f"airtype: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"airtype: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {command}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airtype",
        description="Local push-to-talk dictation. Run with no arguments for the settings menu.",
    )
    parser.add_argument("--version", action="version", version=f"airtype {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("service", help="run the always-on dictation service (systemd ExecStart)")
    subparsers.add_parser("toggle", help="start/stop recording in the running service")

    status = subparsers.add_parser("status", help="show the running service status")
    status.add_argument("--json", action="store_true")

    record = subparsers.add_parser("record", help="one-shot: record until Enter, then paste")
    _add_output_options(record)

    transcribe = subparsers.add_parser("transcribe", help="transcribe an audio file")
    transcribe.add_argument("path", type=Path)
    _add_output_options(transcribe)

    setup = subparsers.add_parser("setup", help="download the active (or given) model")
    setup.add_argument("--model", choices=tuple(MODEL_REGISTRY), help="model to download and activate")
    setup.add_argument("--yes", action="store_true", help="download without prompting")
    setup.add_argument("--model-dir", help="parent directory that stores model folders")
    setup.add_argument("--json", action="store_true", help="print machine-readable status")

    subparsers.add_parser("models", help="list available models")

    settings = subparsers.add_parser("settings", help="show or change settings non-interactively")
    settings.add_argument("--json", action="store_true")
    settings.add_argument("--paste-mode")
    settings.add_argument("--start-hotkey")
    settings.add_argument("--stop-key")
    settings.add_argument("--model", choices=tuple(MODEL_REGISTRY))
    settings.add_argument("--model-dir")

    doctor = subparsers.add_parser("doctor", help="check the local setup")
    doctor.add_argument("--fix", action="store_true", help="kept for compatibility; no-op")

    subparsers.add_parser("install", help="install and start the systemd user service")
    subparsers.add_parser("uninstall", help="stop and remove the systemd user service")
    return parser


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--paste",
        choices=("auto", "copy-only", "ctrl-v", "ctrl-shift-v", "cmd-v", "cmd-shift-v"),
        help="paste after copying",
    )
    parser.add_argument(
        "--unload-timeout",
        type=float,
        default=None,
        help="seconds to keep the model loaded after transcription in this process",
    )
    parser.add_argument("--no-copy", action="store_true", help="print only")
    parser.add_argument("--quiet", action="store_true", help="only print transcript text")


def _pipeline(args) -> AirtypePipeline:
    config = load_config()
    paste_mode = normalize_paste_mode(args.paste or config["paste_mode"])
    unload_timeout = 0 if args.unload_timeout is None else max(0, args.unload_timeout)
    return AirtypePipeline(
        paste_mode=paste_mode,
        copy=not args.no_copy,
        unload_timeout_seconds=unload_timeout,
        paste_fallback=config["paste_fallback"],
        terminal_classes=config["terminal_classes"],
        sounds_enabled=config["sounds_enabled"],
    )


def _service(args) -> int:
    return AirtypeService().run()


def _toggle(args) -> int:
    response = request("toggle")
    if not response.get("ok"):
        print(f"airtype: {response.get('error')}", file=sys.stderr)
        return 1
    print(response["result"]["state"])
    return 0


def _status(args) -> int:
    response = request("status")
    if not response.get("ok"):
        print(f"airtype: {response.get('error')}", file=sys.stderr)
        return 1
    status = response["result"]
    if args.json:
        print(json.dumps(status, sort_keys=True))
        return 0
    for key in (
        "state",
        "model",
        "model_loaded",
        "listener",
        "hotkey",
        "paste_mode",
        "uptime_seconds",
        "pid",
    ):
        print(f"{key}: {status.get(key)}")
    return 0


def _record(args) -> int:
    pipeline = _pipeline(args)
    if not args.quiet:
        print("Recording now. Press Enter to stop.", file=sys.stderr)
        print("Model loads in the background while audio is captured.", file=sys.stderr)
    session = pipeline.start_recording()
    input()
    result = pipeline.stop_recording(session)
    _print_result(result, args.quiet)
    return 0 if result.text else 1


def _transcribe(args) -> int:
    if not _ensure_model_ready(interactive=sys.stdin.isatty()):
        return 1
    pipeline = _pipeline(args)
    result = pipeline.transcribe_path(args.path)
    _print_result(result, args.quiet)
    return 0 if result.text else 1


def _setup(args) -> int:
    changes = {}
    if args.model:
        changes["model"] = args.model
    if args.model_dir:
        changes["model_dir"] = str(resolve_custom_model_base(args.model_dir))
    if changes:
        update_config(**changes)

    spec = get_model_spec(load_config()["model"])
    model_dir = default_model_dir(spec)
    if not model_exists(spec, model_dir):
        if not args.yes and sys.stdin.isatty():
            print(f"Model: {spec.dir_name}")
            print(f"Size: {spec.size_label}")
            print(f"Destination: {model_dir}")
            answer = input("Press Enter/Y to download, or n to cancel: ").strip().lower()
            if answer in {"n", "no", "q", "quit", "cancel"}:
                print("Model download cancelled.")
                return 1
        elif not args.yes:
            print(
                "Airtype model is missing. Run `airtype setup` in a terminal or use `airtype setup --yes`.",
                file=sys.stderr,
            )
            return 1
        ensure_model_download(spec, model_dir, progress=sys.stdout.isatty())

    update_config(model_download_approved=True)
    settings = public_settings()
    if args.json:
        print(json.dumps(settings, sort_keys=True))
    else:
        print(f"Airtype model is ready: {settings['model_dir']}")
    return 0


def _models(args) -> int:
    config = load_config()
    for spec in MODEL_REGISTRY.values():
        from .config import configured_model_dir

        markers = []
        if spec.name == config["model"]:
            markers.append("active")
        if model_exists(spec, configured_model_dir(spec.dir_name)):
            markers.append("downloaded")
        marker = f" [{', '.join(markers)}]" if markers else ""
        print(f"{spec.name}{marker}")
        print(f"    {spec.display} | {spec.langs} | {spec.size_label}")
    return 0


def _settings(args) -> int:
    changes = {}
    if args.paste_mode:
        changes["paste_mode"] = args.paste_mode
    if args.start_hotkey:
        changes["start_hotkey"] = args.start_hotkey
    if args.stop_key:
        changes["stop_key"] = args.stop_key
    if args.model:
        changes["model"] = args.model
    if args.model_dir:
        changes["model_dir"] = str(resolve_custom_model_base(args.model_dir))
    if changes:
        update_config(**changes)
        try:
            request("reload-config")
        except ServiceNotRunningError:
            pass

    settings = public_settings()
    if args.json:
        print(json.dumps(settings, sort_keys=True))
    else:
        print(f"Config: {settings['config_path']}")
        print(f"Model: {settings['model']} ({settings['model_display']})")
        print(f"Model dir: {settings['model_dir']} (ready: {'yes' if settings['model_exists'] else 'no'})")
        print(f"Paste mode: {settings['paste_label']} (fallback: {settings['paste_fallback']})")
        print(f"Hotkey: hold+double-tap {settings['start_hotkey']}, stop with {settings['stop_key']}")
        print(f"Sounds: {'on' if settings['sounds_enabled'] else 'off'}")
    return 0


def _install(args) -> int:
    from .systemd import install_service

    unit_path = install_service()
    print(f"Installed and started: {unit_path}")
    print("Check it with: systemctl --user status airtype")
    return 0


def _uninstall(args) -> int:
    from .systemd import uninstall_service

    uninstall_service()
    print("airtype systemd user service removed.")
    return 0


def run_doctor(fix: bool = False) -> int:
    from .systemd import UNIT_PATH, service_state

    settings = public_settings()
    checks: list[tuple[str, bool, str]] = []

    in_input_group = _in_input_group()
    checks.append(("input group (evdev hotkeys)", in_input_group, "sudo usermod -aG input $USER, then log out/in"))
    checks.append(("wl-copy (clipboard)", shutil.which("wl-copy") is not None, "install wl-clipboard"))
    checks.append(("wtype (paste keystroke)", shutil.which("wtype") is not None, "install wtype"))
    hypr = shutil.which("hyprctl") is not None
    checks.append(("hyprctl (terminal-aware paste)", hypr, "auto paste falls back to a fixed mode"))
    checks.append(("audio player (pw-play)", any(shutil.which(c) for c in ("pw-play", "paplay", "ffplay", "aplay")), "install pipewire-audio or alsa-utils"))
    checks.append(("model files", settings["model_exists"], "run: airtype setup"))
    checks.append(("sherpa-onnx import", _sherpa_import_ok(), "reinstall: uv tool install --reinstall airtype"))

    socket_ok = socket_path().exists()
    checks.append(("service socket", socket_ok, "start with: systemctl --user start airtype"))
    unit_installed = UNIT_PATH.exists()
    checks.append(("systemd unit", unit_installed, "run: airtype install"))

    print(f"airtype {__version__} on {_os_info()} ({os.environ.get('XDG_SESSION_TYPE') or 'n/a'})")
    print(f"Config: {settings['config_path']}")
    print(f"Model: {settings['model']} at {settings['model_dir']}")
    print(f"Paste: {settings['paste_label']} | backend: {describe_auto_paste_backend(settings['paste_fallback'])}")
    if hypr:
        print(f"Focused window class: {active_window_class() or 'n/a'}")
    if unit_installed:
        print(f"Systemd unit: {UNIT_PATH} ({service_state()})")
    print()
    failed = 0
    for label, ok, hint in checks:
        mark = "ok " if ok else "FAIL"
        suffix = "" if ok else f"  -> {hint}"
        if not ok:
            failed += 1
        print(f"[{mark}] {label}{suffix}")
    return 0 if failed == 0 else 1


def _in_input_group() -> bool:
    try:
        return any(g.gr_name == "input" for g in map(grp.getgrgid, os.getgroups()))
    except (KeyError, OSError):
        return False


def _sherpa_import_ok() -> bool:
    try:
        import sherpa_onnx  # noqa: F401

        return True
    except Exception:
        return False


def _ensure_model_ready(interactive: bool) -> bool:
    if model_exists():
        return True
    if not interactive:
        print(
            "Airtype model is missing. Run `airtype setup` in a terminal or use `airtype setup --yes`.",
            file=sys.stderr,
        )
        return False
    args = argparse.Namespace(yes=False, model=None, model_dir=None, json=False)
    return _setup(args) == 0


def _os_info() -> str:
    system = platform.system()
    if system == "Linux":
        os_release = Path("/etc/os-release")
        if os_release.exists():
            data = {}
            for line in os_release.read_text(errors="ignore").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    data[key] = value.strip('"')
            return data.get("PRETTY_NAME") or data.get("NAME") or system
    return f"{system} {platform.release()}".strip()


def _print_result(result, quiet: bool) -> None:
    if quiet:
        print(result.text)
        return

    print(result.text)
    details = [
        f"{result.elapsed_seconds:.2f}s",
        "loaded" if result.loaded_now else "warm",
        "unloaded" if result.unloaded else "kept-loaded",
    ]
    if result.copied:
        details.append("copied")
    if result.pasted:
        details.append(f"pasted:{result.paste_backend}")
    print(f"[{', '.join(details)}]", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
