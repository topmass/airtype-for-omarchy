import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .asr import SAMPLE_RATE, transcribe_array, transcribe_file
from .clipboard import auto_paste, copy_to_clipboard
from .config import normalize_paste_mode
from .model_manager import ModelManager
from .sounds import SOUND_START, SOUND_STOP, play_sound, play_sound_later

LIVE_MIN_SEGMENT_SECONDS = 6
LIVE_MAX_SEGMENT_SECONDS = 25
LIVE_SILENCE_SECONDS = 0.7
LIVE_VAD_FRAME_SECONDS = 0.1
LIVE_SILENCE_RMS = 0.008


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    copied: bool
    pasted: bool
    paste_backend: str
    elapsed_seconds: float
    loaded_now: bool
    unloaded: bool


class AirtypePipeline:
    def __init__(
        self,
        manager: ModelManager | None = None,
        paste_mode: str = "copy_only",
        copy: bool = True,
        unload_timeout_seconds: float = 0,
    ) -> None:
        self.manager = manager or ModelManager()
        self.paste_mode = normalize_paste_mode(paste_mode)
        self.copy = copy
        self.unload_timeout_seconds = unload_timeout_seconds

    def transcribe_path(self, path: Path | str) -> TranscriptResult:
        started = time.monotonic()
        loaded_now = False
        with self.manager.borrow() as (model, loaded):
            loaded_now = loaded
            text = transcribe_file(model, Path(path))
        return self._finish(text, started, loaded_now)

    def start_recording(self) -> "RecordingSession":
        play_sound(SOUND_START)
        session = RecordingSession(self.manager)
        session.start()
        return session

    def stop_recording(self, session: "RecordingSession") -> TranscriptResult:
        started = time.monotonic()
        text, loaded_now = session.stop()
        result = self._finish(text, started, loaded_now)
        if result.text:
            play_sound_later(SOUND_STOP)
        return result

    def _finish(self, text: str, started: float, loaded_now: bool) -> TranscriptResult:
        text = text.strip()
        copied = copy_to_clipboard(text) if self.copy and text else False
        pasted = False
        paste_backend = "copy_only"
        if copied:
            pasted, paste_backend = auto_paste(self.paste_mode)
        self.manager.touch()
        unloaded = self.manager.unload_if_idle(self.unload_timeout_seconds)
        return TranscriptResult(
            text=text,
            copied=copied,
            pasted=pasted,
            paste_backend=paste_backend,
            elapsed_seconds=time.monotonic() - started,
            loaded_now=loaded_now,
            unloaded=unloaded,
        )


class RecordingSession:
    def __init__(self, manager: ModelManager) -> None:
        self.manager = manager
        self._audio_data: list[np.ndarray] = []
        self._audio_lock = threading.RLock()
        self._streaming_stop = threading.Event()
        self._streaming_thread: threading.Thread | None = None
        self._streaming_texts: list[str] = []
        self._streaming_error: str | None = None
        self._streaming_processed_frames = 0
        self._stream = None
        self._loaded_now = False

    def start(self) -> None:
        import sounddevice as sd

        with self._audio_lock:
            self._audio_data = []
            self._streaming_texts = []
            self._streaming_error = None
            self._streaming_processed_frames = 0
        self._streaming_stop.clear()
        self._stream = sd.InputStream(
            channels=1,
            samplerate=SAMPLE_RATE,
            dtype="float32",
            callback=self._audio_cb,
        )
        self._stream.start()
        self._streaming_thread = threading.Thread(
            target=self._streaming_transcribe,
            name="airtype-streaming-asr",
            daemon=True,
        )
        self._streaming_thread.start()

    def stop(self) -> tuple[str, bool]:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        self._streaming_stop.set()
        if self._streaming_thread is not None:
            self._streaming_thread.join()
            self._streaming_thread = None

        audio = self._snapshot_audio()
        if len(audio) == 0:
            return "", self._loaded_now

        with self._audio_lock:
            streaming_error = self._streaming_error
            streaming_texts = list(self._streaming_texts)
            processed_frames = self._streaming_processed_frames

        text_parts = []
        if streaming_error:
            processed_frames = 0
        else:
            text_parts.extend(streaming_texts)

        final_audio = audio[processed_frames:]
        if len(final_audio) > 0:
            with self.manager.borrow() as (model, loaded_now):
                self._loaded_now = self._loaded_now or loaded_now
                final_text = transcribe_array(model, final_audio, SAMPLE_RATE)
                if final_text:
                    text_parts.append(final_text)

        text = " ".join(part.strip() for part in text_parts if part.strip())
        return text, self._loaded_now

    def _audio_cb(self, indata, frames, time_info, status) -> None:
        with self._audio_lock:
            self._audio_data.append(indata.copy())

    def _snapshot_audio(self) -> np.ndarray:
        with self._audio_lock:
            if not self._audio_data:
                return np.array([], dtype=np.float32)
            return np.concatenate(self._audio_data, axis=0).flatten().astype(np.float32)

    def _find_segment_end(self, audio: np.ndarray, start_frame: int) -> int | None:
        available_frames = len(audio) - start_frame
        if available_frames <= 0:
            return None

        min_frames = SAMPLE_RATE * LIVE_MIN_SEGMENT_SECONDS
        max_frames = SAMPLE_RATE * LIVE_MAX_SEGMENT_SECONDS
        if available_frames < min_frames:
            return None

        frame_size = max(1, int(SAMPLE_RATE * LIVE_VAD_FRAME_SECONDS))
        silence_frames = int(SAMPLE_RATE * LIVE_SILENCE_SECONDS)
        search_end = start_frame + min(available_frames, max_frames)
        silence_run = 0

        for pos in range(start_frame + min_frames, search_end - frame_size + 1, frame_size):
            frame = audio[pos : pos + frame_size]
            rms = float(np.sqrt(np.mean(frame * frame))) if len(frame) else 0.0
            if rms < LIVE_SILENCE_RMS:
                silence_run += frame_size
                if silence_run >= silence_frames:
                    return pos + frame_size
            else:
                silence_run = 0

        if available_frames >= max_frames:
            return start_frame + max_frames
        return None

    def _streaming_transcribe(self) -> None:
        try:
            with self.manager.borrow() as (model, loaded_now):
                self._loaded_now = self._loaded_now or loaded_now
                while not self._streaming_stop.is_set():
                    audio = self._snapshot_audio()
                    start = self._streaming_processed_frames
                    end = self._find_segment_end(audio, start)
                    if end is None:
                        time.sleep(0.25)
                        continue

                    text = transcribe_array(model, audio[start:end], SAMPLE_RATE)
                    with self._audio_lock:
                        if text:
                            self._streaming_texts.append(text)
                        self._streaming_processed_frames = end
        except Exception as exc:
            with self._audio_lock:
                self._streaming_error = str(exc)


def write_temp_wav(audio: np.ndarray) -> Path:
    temp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp.close()
    path = Path(temp.name)
    sf.write(path, audio, SAMPLE_RATE)
    return path
