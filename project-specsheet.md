# Airtype Project Specsheet (v2)

Airtype is a pure-Python local speech-to-text dictation service for Omarchy / Hyprland (and other Linux desktops). One uv-managed package owns everything: evdev hotkeys, microphone capture, sherpa-onnx transcription, clipboard/paste, the unix-socket control channel, the systemd unit, and the gum settings menu. There is no Node/Bun layer; v1's OpenTUI front end was removed on purpose.

## Install and commands

- `uv tool install <repo>` puts `airtype` at `~/.local/bin/airtype` (standalone venv, no uv needed at runtime)
- `airtype` (no args, TTY) - gum settings menu (`src/airtype/menu.py`)
- `airtype service` - foreground daemon; this is the systemd ExecStart
- `airtype toggle | status [--json]` - IPC clients
- `airtype setup [--model KEY] [--yes]` - download + activate a model
- `airtype models | settings | doctor | install | uninstall`
- Dev: `uv sync && uv run python -m unittest discover tests` (51 tests)

## Architecture and file map

All source in `src/airtype/`:

- `service.py` - `AirtypeService`. Main loop consumes a queue fed by IPC and hotkey events. States: ready → recording → transcribing → ready/unloaded. Listener self-heal (dead threads, >10s suspend/resume gap). Logs human-readable lines to stdout for journald. `AIRTYPE_DEBUG=1` logs every key event.
- `hotkey.py` - `EvdevHotkeyListener` (one thread per keyboard device, select() 0.2s, no grab, reports ALL keys) and `HotkeyPolicy`, the pure state machine: hold start-modifiers + double-tap tap-key starts; a clean single stop-key tap stops. Chord protection (any other key during the stop tap marks it dirty, so Alt+Tab never stops), 0.25s stop cooldown, 0.5s super-release tolerance.
- `ipc.py` - unix socket at `$XDG_RUNTIME_DIR/airtype/control.sock`, newline JSON. Commands: toggle, status, reload-config, quit, subscribe (event stream). Single-instance guard by probing an existing socket. Client helpers `request()` / `subscribe_events()`.
- `pipeline.py` - `AirtypePipeline` + `RecordingSession`. Mic opens instantly; the model loads in a background thread while audio accumulates; RMS-VAD pause-aware chunking (6-25s segments, 0.7s silence cut) transcribes during recording; stop transcribes only the tail. `pre_paste` hook waits for physical modifier release before the synthetic paste keystroke.
- `asr.py` - sherpa-onnx loading (`from_transducer` for parakeet, `from_moonshine_v2` for moonshine), 16 kHz, chunked batch decode, model download (k2-fsa GitHub tarball with HF per-file fallback).
- `registry.py` - `ModelSpec` table. Default `parakeet-unified-en-0.6b-int8`; also parakeet v3 (multilingual), v2 (legacy), moonshine-base-en (light). Add models here: name, kind, dir_name, files, hf_repo.
- `model_manager.py` - refcounted `borrow()` context manager; `unload_if_idle` frees RAM after each transcription (service uses timeout 0 → always unload).
- `terminal.py` - `active_window_class()` via `hyprctl activewindow -j`; `resolve_paste_mode()` resolves paste mode "auto" at paste time: terminal class → ctrl_shift_v, other → ctrl_v, no hyprctl → `paste_fallback`.
- `clipboard.py` - wl-copy copy; wtype paste (ydotool/xdotool/pynput fallbacks).
- `config.py` - schema v2 at `~/.config/airtype/config.json` with `migrate_config()` from v1. Keys: model, model_dir (PARENT dir of model folders), paste_mode/paste_fallback, start_hotkey/stop_key, thresholds, terminal_classes, sounds_enabled. Any change + `reload-config` over IPC hot-applies.
- `menu.py` - gum choose/confirm/input with plain-stdin fallback. Model swap/download/delete, paste, hotkey, terminal classes, sounds, restart, doctor.
- `systemd.py` - renders/installs `~/.config/systemd/user/airtype.service` (WantedBy graphical-session.target, ExecStart is the resolved airtype path).
- `sounds.py` - start/stop mp3s from package data (`airtype/assets/`) via pw-play/paplay/ffplay/aplay.

## Rules that keep this working

1. **Never reintroduce an onnxruntime dependency or the libonnxruntime symlink hack.** sherpa-onnx >= 1.13.6 wheels bundle their own `libonnxruntime.so` inside `sherpa_onnx/lib/`. Symlinking over it breaks the import with `VERS_x not found`. If the sherpa import breaks, reinstall the tool (`uv tool install --reinstall`).
2. **Listener threads must never take the service lock.** `_on_key` only feeds `HotkeyPolicy` (under `_policy_lock`) and enqueues `_hotkey_start`/`_hotkey_stop` for the main loop. If key handling ever blocks on transcription, the modifier-release wait before paste deadlocks.
3. **The evdev listener reports all keys, not just hotkeys** - dirty-tap chord detection depends on it. Device selection still keys on the hotkey codes.
4. **Paste-mode "auto" resolves at paste time**, not at config time, so it follows the focused window.
5. **`model_dir` in config is a parent directory** holding one folder per model (v1 stored the model folder itself; migration strips the leaf).
6. **The service holds no model when idle** - target is ~0% CPU and low RSS. `release_freed_memory()` (glibc `malloc_trim`) must run after unload AND again after the recording session ends (`service._stop_recording`), or RSS parks at the model's ~500 MB peak. Post-cycle idle is ~100 MB. Do not add background timers that keep the model warm by default.
6b. **Keyboard hotplug**: the evdev listener only enumerates devices when it starts. `_restart_listener_on_hotplug` watches the `/dev/input` directory mtime each idle tick and restarts the listener when the matching device set changes (wireless dongles and Bluetooth keyboards often (re)appear after suspend/resume - this was a real field failure). Do not remove it.
7. Hotkey testing without a human: create an `evdev.UInput` keyboard, then (re)start the service (the listener enumerates devices at startup), then write KEY_LEFTMETA/KEY_LEFTALT events. Run detached (nohup); simulated Super/Alt reaches the real compositor.
8. Omarchy notes: the old voxtype tool was removed with `systemctl --user disable --now voxtype`, deleting `~/.config/systemd/user/voxtype.service`, `~/.config/omarchy/hooks/post-update.d/install-voxtype.hook`, `~/.config/voxtype`, `~/.local/share/voxtype`, plus `sudo pacman -Rns voxtype-bin`. Omarchy's F9 binds and bar indicator disappear on their own once the voxtype binary is gone.

## Verified on 2026-08-27 (Omarchy, Dell XPS 16, Wayland/Hyprland)

- 51 unit tests green; wheel ships mp3 assets
- Parakeet-unified transcribes the bundled test wav in ~1.5s cold (load+decode+unload); moonshine in ~0.5s
- Kernel-level simulated hotkey: Super+double-Alt starts, Alt+Tab ignored, clean Alt tap stops
- systemd service: 26 MB idle RSS, 1 CPU tick per 10s idle, evdev listener, single-instance guard, live config reload, journald logging
