# Airtype

Fast local speech-to-text for rambling into AI tools. Airtype records immediately, loads the ASR model in the background, transcribes pause-aware chunks while you keep talking, copies the result, optionally pastes it, and unloads the model by default.

Airtype does not ship a model. On first run it downloads the sherpa-onnx Parakeet V2 INT8 English model into the user cache.

## Install

Airtype is designed to be installable as an npm CLI after publishing:

```bash
pnpm add -g airtype
airtype
```

One-off runs also work:

```bash
pnpm dlx airtype
```

The npm package exposes an `airtype` command through `package.json#bin`. The launcher uses `uv` for the Python speech engine and Bun for the OpenTUI front end.

Before running Airtype, install:

- `uv`: https://docs.astral.sh/uv/getting-started/installation/
- `bun`: https://bun.com/docs/installation/
- `ffmpeg`: optional, only needed when transcribing audio files that are not already readable as 16 kHz audio

OpenTUI is currently Bun-exclusive, with Node support still in progress: https://opentui.com/docs/getting-started/

## First Run

Run:

```bash
airtype
```

If the model is missing, Airtype prints the model name, approximate download size, and destination directory. Press `Enter` or `Y` to use the default cache location, paste a custom directory, or type `n` to cancel.

Default model cache locations are OS-native through `platformdirs`:

- Linux: `~/.cache/airtype/models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`
- macOS: `~/Library/Caches/airtype/models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`
- Windows: `%LOCALAPPDATA%\airtype\Cache\models\sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`

Check the exact path on any machine:

```bash
airtype settings --json
```

The model download tries the sherpa-onnx GitHub release archive first, then falls back to Hugging Face:

- `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2`
- `https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`

## Usage

Launch the TUI:

```bash
airtype
```

Controls:

- `Enter` or `Space`: start or stop recording
- Double-tap the configured global hotkey: start or stop recording from anywhere
- `p`: cycle paste mode
- `h`: cycle hotkey
- `q`: quit

Paste modes:

- `Ctrl+Shift+V`
- `Ctrl+V`
- `Command+V`
- `Command+Shift+V`
- `Copy only`

CLI modes:

```bash
airtype record
airtype record --paste ctrl-v
airtype record --paste copy-only
airtype transcribe ./audio.wav --no-copy
airtype service
airtype doctor
```

Airtype unloads the model after each transcription by default. For short bursts, keep it warm:

```bash
airtype record --unload-timeout 300
```

## Paste Setup

Clipboard copy and auto-paste are OS-specific.

Linux Wayland:

```bash
# Fedora/RHEL
sudo dnf install wl-clipboard ydotool wtype xclip xdotool

# Arch
sudo pacman -S wl-clipboard ydotool wtype xclip xdotool

# Debian/Ubuntu
sudo apt install wl-clipboard ydotool wtype xclip xdotool
```

Wayland auto-paste prefers `wtype` when it works and falls back to `ydotool`. KDE Wayland usually needs `ydotool`. `ydotoold` must be running and its socket must be readable by your user.

Linux X11 uses `xclip` or `xsel` for copy and `xdotool` for paste.

macOS uses `pbcopy` for copy and `pynput` for paste. Grant Accessibility permission to the terminal app that runs Airtype.

Windows uses `clip` for copy and `pynput` for paste. No extra paste package is usually required.

Run:

```bash
airtype doctor
```

to see the active OS, model path, paste mode, and detected paste backend.

## Development

```bash
uv run python -m unittest discover -s tests -v
pnpm audit --prod
bun src/tui/airtype-tui.ts
```

Package dry run:

```bash
pnpm pack --dry-run
```

Airtype sets `UV_PROJECT_ENVIRONMENT` to a user-cache venv when launched through the npm bin. That keeps the Python environment writable even when the npm package is installed globally.

