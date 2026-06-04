# Airtype Project Specsheet

Airtype is a local speech-to-text utility for fast dictation. The Python package owns microphone capture, sherpa-onnx Parakeet V2 transcription, clipboard/paste, and model unload behavior. The OpenTUI front end renders status and delegates recording control to the Python service backend.

## Commands

- `uv run airtype record --paste ctrl-shift-v`: one-shot record until Enter, then copy/paste and exit
- `uv run airtype service --paste ctrl-shift-v`: long-running JSON backend with global double-tap hotkey control
- `bun src/tui/airtype-tui.ts`: OpenTUI front end that launches the service backend during development
- `node bin/airtype.js` or installed `airtype`: npm-bin launcher for users
- Local shell alias: `airtype`

## Current Alias

`/home/topmass/.bashrc` currently defines a local development alias:

```bash
alias airtype='cd /home/topmass/Code/tools/airtype && /home/topmass/.bun/bin/bun src/tui/airtype-tui.ts'
```

The packaged path is `package.json#bin`, which exposes `airtype` through `bin/airtype.js` after global npm/pnpm installation.

## Backend Flow

1. `AirtypePipeline.start_recording()` starts the microphone immediately.
2. The start sound plays immediately before microphone capture starts.
3. `RecordingSession` loads the model in a background transcription thread while audio is already being captured.
4. Completed pause-aware segments are transcribed while recording continues.
5. On stop, only the remaining tail is transcribed.
6. `AirtypePipeline._finish()` copies text, optionally auto-pastes, touches the model manager, and applies the unload timeout.
7. The stop sound is scheduled only after final text exists and copy/paste handling has finished.

## Files

- `src/airtype/asr.py`: sherpa-onnx Parakeet V2 INT8 model download, loading, and transcription
- `src/airtype/pipeline.py`: recording, pause-aware segmentation, finalization, copy/paste, unload
- `src/airtype/model_manager.py`: model load/borrow/unload lifecycle
- `src/airtype/config.py`: OS-native config/cache paths, paste mode settings, hotkey settings
- `src/airtype/clipboard.py`: `wl-copy`, `xclip`, `xsel`, `wtype`, `ydotool`, and `xdotool` integration
- `src/airtype/hotkey.py`: global hotkey normalization and evdev/pynput listener
- `src/airtype/service.py`: long-running JSON service with double-tap Alt control
- `src/airtype/sounds.py`: local start sound playback and deferred stop sound playback
- `src/tui/airtype-tui.ts`: OpenTUI front end that spawns the Python service
- `bin/airtype.js`: npm-bin launcher; checks for uv/Bun and sets `UV_PROJECT_ENVIRONMENT`
- `package.json` and `pnpm-lock.yaml`: pinned OpenTUI dependency metadata
- `soundfx/start.mp3` and `soundfx/stop.mp3`: copied from RecordTranscribe

## Runtime Dependencies

- Python dependencies are managed with `uv`.
- OpenTUI is managed with `pnpm` and run with Bun.
- Bun is installed locally at `/home/topmass/.bun/bin/bun` because OpenTUI is currently Bun-first.
- `@opentui/core` is pinned to `0.2.16`; it was selected because it was published more than seven days before this work and `pnpm audit --prod` reported no known vulnerabilities.
- `evdev` is Linux-only in `pyproject.toml`; keep its environment marker so Windows/macOS installs do not fail before runtime.
- The npm launcher points uv's project virtual environment at the user cache (`UV_PROJECT_ENVIRONMENT`) so a global install does not need write access to the installed package directory.

## Model Storage

Airtype does not ship the ASR model. First run prompts before downloading the default model.

Default Linux model cache:

```text
~/.cache/airtype/models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8
```

Typical macOS and Windows cache paths are:

```text
~/Library/Caches/airtype/models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8
%LOCALAPPDATA%\airtype\Cache\models\sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8
```

The exact runtime path is reported by `airtype settings --json` and `airtype doctor`.

`AIRTYPE_MODEL_DIR` overrides the model directory. `airtype setup --model-dir <dir>` and `airtype settings --model-dir <dir>` persist a custom model parent or exact model directory.

## Rules

- Keep ASR model ownership in Python; OpenTUI should only be a front end.
- Keep `airtype record` working as the simple fallback path.
- Global hotkeys should prefer `evdev` on Wayland and fall back to `pynput`.
- Do not keep the model loaded unless `--unload-timeout` is explicitly set above `0`.
- Do not silently download the model from noninteractive commands. Downloads should happen through the first-run prompt or explicit `airtype setup --yes`.
- Keep npm dependencies pinned and audited. `@opentui/core` is pinned to `0.2.16`.
- Keep the npm package small: never include `.models`, `.venv`, or `node_modules` in published files.
- Start sound belongs to recording start, not model load completion.
- Stop sound belongs after copy/paste completion, not when recording stops. It should be scheduled asynchronously so audio-player startup never sits on the paste-critical path.

## Verification

- `uv run python -m py_compile src/airtype/*.py`
- `uv run python -m unittest discover -s tests -v`
- `pnpm audit --prod`
- `node --check bin/airtype.js`
- `bun build src/tui/airtype-tui.ts --target bun --outdir /tmp/airtype-tui-build`
- `UV_PROJECT_ENVIRONMENT=/tmp/airtype-uv-venv-check node bin/airtype.js settings --json`
- `uv run airtype service --paste ctrl-shift-v --no-copy --unload-timeout 0` was driven through JSON stdin and verified `ready -> recording -> transcribing -> unloaded`.
- `bash -ic 'airtype'` launched the OpenTUI screen, reported `Global listener: evdev`, and quit cleanly with `q`.
- OpenTUI Enter toggle was verified with `AIRTYPE_TUI_NO_COPY=1`: `ready -> recording -> transcribing -> unloaded`.
- Sound asset paths were verified in the `uv` environment.
- Unit coverage verifies stop sound order: copy, paste, then sound.
