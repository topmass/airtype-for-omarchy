# Airtype

Fast local push-to-talk speech-to-text for Linux, built for [Omarchy](https://omarchy.org) / Hyprland but usable on any Wayland or X11 desktop.

Hold **Super** and double-tap **Alt** to start recording. Tap **Alt** once to stop. Airtype transcribes locally, copies the text, and pastes it into the focused window. If the focused window is a terminal (foot, ghostty, alacritty, kitty, ...), it pastes with Ctrl+Shift+V; everywhere else it uses Ctrl+V.

Airtype records immediately and loads the ASR model in the background while you talk. It transcribes pause-aware chunks during the recording and unloads the model when done, so the idle service uses about 26 MB of RAM and ~0% CPU.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
git clone https://github.com/topmass/AirType ~/airtype
uv tool install ~/airtype
airtype setup      # downloads the model (~480 MB) on first run
airtype install    # installs + starts the systemd user service
airtype doctor     # verify everything is green
```

Requirements on Wayland: `wl-clipboard`, `wtype`, and your user in the `input` group (for global hotkeys via evdev):

```bash
sudo pacman -S wl-clipboard wtype
sudo usermod -aG input $USER   # then log out and back in
```

## Usage

- `airtype` - settings menu (gum-based): model swap/download/delete, paste mode, hotkey, terminal classes, sounds
- `airtype toggle` / `airtype status` - control the running service
- `airtype record` - one-shot dictation in a terminal (Enter to stop)
- `airtype transcribe file.wav` - transcribe an audio file
- `airtype models` - list available models
- `airtype settings --json` - scriptable settings
- `airtype doctor` - health checks

Config lives at `~/.config/airtype/config.json` and hot-reloads into the running service.

## Models

All models run on CPU via sherpa-onnx int8 ONNX:

| Key | Model | Notes |
|---|---|---|
| `parakeet-unified-en-0.6b-int8` (default) | NVIDIA Parakeet Unified EN 0.6B | English, punctuation + capitalization |
| `parakeet-tdt-0.6b-v3-int8` | Parakeet TDT v3 | 25 European languages |
| `parakeet-tdt-0.6b-v2-int8` | Parakeet TDT v2 | legacy, no punctuation |
| `moonshine-base-en` | Moonshine Base EN | 145 MB light tier for weak hardware |

Models download on demand from the sherpa-onnx release page into `~/.cache/airtype/models/`.

## How the hotkey works

The service reads `/dev/input` key events directly (evdev), below the compositor, so hotkeys work in any app including fullscreen windows and nothing is grabbed - your keys still reach applications normally. The state machine ignores chords like Alt+Tab while recording, tolerates releasing Super slightly before the second Alt tap, and waits for your modifiers to be released before sending the paste keystroke.
