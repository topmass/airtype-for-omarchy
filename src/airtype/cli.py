import argparse
import json
import os
import platform
import sys
from pathlib import Path

from .asr import DEFAULT_MODEL, default_model_dir, ensure_model_download, model_exists
from .clipboard import describe_auto_paste_backend
from .config import (
    MODEL_SIZE_LABEL,
    PASTE_MODE_LABELS,
    cycle_hotkey,
    cycle_paste_mode,
    load_config,
    normalize_paste_mode,
    public_settings,
    resolve_custom_model_dir,
    save_config,
    update_config,
)
from .hotkey import format_hotkey, parse_hotkey_keys
from .pipeline import AirtypePipeline
from .service import AirtypeService


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["record"]

    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command

    try:
        if command == "doctor":
            return _doctor(args)
        if command == "transcribe":
            return _transcribe(args)
        if command == "record":
            if not _ensure_model_ready(interactive=sys.stdin.isatty()):
                return 1
            return _record(args)
        if command == "service":
            return _service(args)
        if command == "setup":
            return _setup(args)
        if command == "settings":
            return _settings(args)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"airtype: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {command}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="airtype")
    subparsers = parser.add_subparsers(dest="command")

    record = subparsers.add_parser("record", help="record microphone audio until Enter")
    _add_output_options(record)

    service = subparsers.add_parser("service", help="run the hotkey backend")
    _add_output_options(service)
    service.add_argument("--hotkey", help="double-tap key or combo")

    transcribe = subparsers.add_parser("transcribe", help="transcribe an audio file")
    transcribe.add_argument("path", type=Path)
    _add_output_options(transcribe)

    setup = subparsers.add_parser("setup", help="prepare the local model cache")
    setup.add_argument("--yes", action="store_true", help="download without prompting")
    setup.add_argument("--model-dir", help="custom model directory or parent directory")
    setup.add_argument("--json", action="store_true", help="print machine-readable status")

    settings = subparsers.add_parser("settings", help="show or update Airtype settings")
    settings.add_argument("--json", action="store_true")
    paste_choices = tuple(PASTE_MODE_LABELS) + tuple(mode.replace("_", "-") for mode in PASTE_MODE_LABELS)
    settings.add_argument("--paste-mode", choices=paste_choices)
    settings.add_argument("--hotkey")
    settings.add_argument("--model-dir")
    settings.add_argument("--cycle-paste", action="store_true")
    settings.add_argument("--cycle-hotkey", action="store_true")

    subparsers.add_parser("doctor", help="show local backend status")
    return parser


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--paste",
        choices=("copy-only", "ctrl-v", "ctrl-shift-v", "cmd-v", "cmd-shift-v"),
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
    )


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


def _service(args) -> int:
    config = load_config()
    paste_mode = normalize_paste_mode(args.paste or config["paste_mode"])
    hotkey = args.hotkey or config["hotkey"]
    unload_timeout = 0 if args.unload_timeout is None else max(0, args.unload_timeout)
    service = AirtypeService(
        paste_mode=paste_mode,
        unload_timeout_seconds=unload_timeout,
        hotkey=hotkey,
        copy=not args.no_copy,
    )
    return service.run()


def _setup(args) -> int:
    requested_model_dir = resolve_custom_model_dir(args.model_dir, DEFAULT_MODEL) if args.model_dir else None
    active_model_dir = requested_model_dir or default_model_dir()
    if model_exists(active_model_dir):
        if requested_model_dir is not None:
            update_config(model_dir=str(requested_model_dir), model_download_approved=True)
        settings = public_settings(DEFAULT_MODEL)
        if args.json:
            print(json.dumps(settings, sort_keys=True))
        else:
            print(f"Airtype model is ready: {settings['model_dir']}")
        return 0

    model_dir = requested_model_dir or _select_model_dir(args)
    if model_dir is None:
        return 1

    config = load_config()
    config["model_dir"] = str(model_dir)
    config["model_download_approved"] = True
    save_config(config)

    if not args.json:
        print(f"Downloading {DEFAULT_MODEL} to:")
        print(f"  {model_dir}")
    ensure_model_download(model_dir)
    settings = public_settings(DEFAULT_MODEL)
    if args.json:
        print(json.dumps(settings, sort_keys=True))
    else:
        print("Airtype model is ready.")
    return 0


def _settings(args) -> int:
    config = load_config()
    if args.paste_mode:
        config["paste_mode"] = normalize_paste_mode(args.paste_mode)
    if args.hotkey:
        config["hotkey"] = args.hotkey
    if args.model_dir:
        config["model_dir"] = str(resolve_custom_model_dir(args.model_dir, DEFAULT_MODEL))
    if args.cycle_paste:
        config = cycle_paste_mode()
    elif args.cycle_hotkey:
        config = cycle_hotkey()
    elif args.paste_mode or args.hotkey or args.model_dir:
        update_config(**config)
    settings = public_settings(DEFAULT_MODEL)
    if args.json:
        print(json.dumps(settings, sort_keys=True))
    else:
        print(f"Config: {settings['config_path']}")
        print(f"Model: {settings['model_dir']}")
        print(f"Paste mode: {settings['paste_label']}")
        print(f"Hotkey: {format_hotkey(parse_hotkey_keys(settings['hotkey']))}")
    return 0


def _doctor(args) -> int:
    settings = public_settings(DEFAULT_MODEL)
    os_info = _os_info()
    print(f"OS: {os_info}")
    print(f"Session: {(os.environ.get('XDG_SESSION_TYPE') or 'n/a')}")
    print(f"Config: {settings['config_path']}")
    print(f"Model dir: {settings['model_dir']}")
    print(f"Model ready: {'yes' if settings['model_exists'] else 'no'}")
    print(f"Paste mode: {settings['paste_label']}")
    print(f"Paste backend: {describe_auto_paste_backend(settings['paste_mode'])}")
    print(_install_hint())
    return 0


def _select_model_dir(args) -> Path | None:
    if args.model_dir:
        return resolve_custom_model_dir(args.model_dir, DEFAULT_MODEL)

    default_dir = default_model_dir()
    if args.yes or not sys.stdin.isatty():
        if args.yes:
            return default_dir
        print(
            "Airtype model is missing. Run `airtype setup` in an interactive terminal.",
            file=sys.stderr,
        )
        return None

    print("First Airtype run")
    print()
    print("Airtype needs to download the local voice model before transcription works.")
    print(f"Model: {DEFAULT_MODEL}")
    print(f"Size: {MODEL_SIZE_LABEL}")
    print("Default location:")
    print(f"  {default_dir}")
    print()
    answer = input("Press Enter/Y to download here, paste a different directory, or type n to cancel: ").strip()
    if answer.lower() in {"n", "no", "q", "quit", "cancel"}:
        print("Model download cancelled.")
        return None
    if answer == "" or answer.lower() in {"y", "yes"}:
        return default_dir
    return resolve_custom_model_dir(answer, DEFAULT_MODEL)


def _ensure_model_ready(interactive: bool) -> bool:
    if model_exists():
        return True
    if not interactive:
        print(
            "Airtype model is missing. Run `airtype setup` in an interactive terminal or use `airtype setup --yes`.",
            file=sys.stderr,
        )
        return False
    args = argparse.Namespace(yes=False, model_dir=None, json=False)
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


def _install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        return "macOS: grant Accessibility permission for global hotkeys and auto-paste."
    if system == "Windows":
        return "Windows: global hotkeys and paste use pynput; no extra paste package is usually required."

    os_id = ""
    os_release = Path("/etc/os-release")
    if os_release.exists():
        for line in os_release.read_text(errors="ignore").splitlines():
            if line.startswith("ID="):
                os_id = line.split("=", 1)[1].strip().strip('"')
                break
    if os_id in {"fedora", "rhel", "centos"}:
        return "Fedora/RHEL: sudo dnf install wl-clipboard ydotool wtype xclip xdotool"
    if os_id in {"arch", "manjaro"}:
        return "Arch: sudo pacman -S wl-clipboard ydotool wtype xclip xdotool"
    if os_id in {"debian", "ubuntu", "linuxmint", "pop"}:
        return "Debian/Ubuntu: sudo apt install wl-clipboard ydotool wtype xclip xdotool"
    return "Linux: install wl-clipboard plus ydotool or wtype for Wayland, xclip/xdotool for X11."


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
