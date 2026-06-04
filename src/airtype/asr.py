import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from importlib.util import find_spec
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import configured_model_dir

SAMPLE_RATE = 16000
DEFAULT_MODEL = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    f"{DEFAULT_MODEL}.tar.bz2"
)
HF_MODEL_REPO = "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
MODEL_FILES = (
    "encoder.int8.onnx",
    "decoder.int8.onnx",
    "joiner.int8.onnx",
    "tokens.txt",
)


def default_model_dir() -> Path:
    configured = os.environ.get("AIRTYPE_MODEL_DIR")
    if configured:
        return Path(configured).expanduser()

    return configured_model_dir(DEFAULT_MODEL)


def model_exists(model_dir: Path | None = None) -> bool:
    model_dir = model_dir or default_model_dir()
    return all((model_dir / name).is_file() for name in MODEL_FILES)


def ensure_model_download(model_dir: Path | None = None) -> Path:
    model_dir = model_dir or default_model_dir()
    _ensure_model(model_dir)
    return model_dir


def load_model():
    model_dir = default_model_dir()
    _ensure_model(model_dir)
    _prepare_sherpa_onnx_runtime()

    import sherpa_onnx

    threads = int(os.environ.get("AIRTYPE_SHERPA_THREADS", "4"))
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        joiner=str(model_dir / "joiner.int8.onnx"),
        tokens=str(model_dir / "tokens.txt"),
        num_threads=threads,
        model_type="nemo_transducer",
        provider="cpu",
    )
    return recognizer, f"sherpa-parakeet-v2/cpu/int8/{threads}t"


def transcribe_file(model, path: Path | str) -> str:
    temp_path = None
    try:
        audio, sample_rate, temp_path = _read_audio(Path(path))
        return transcribe_array(model, audio, sample_rate)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def transcribe_array(model, audio, sample_rate: int = SAMPLE_RATE) -> str:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(audio) == 0:
        return ""

    chunk_seconds = int(os.environ.get("AIRTYPE_SHERPA_CHUNK_SECONDS", "30"))
    chunk_size = sample_rate * max(1, chunk_seconds)
    streams = []
    for start in range(0, len(audio), chunk_size):
        stream = model.create_stream()
        stream.accept_waveform(sample_rate, np.ascontiguousarray(audio[start : start + chunk_size]))
        streams.append(stream)

    if len(streams) == 1:
        model.decode_stream(streams[0])
    else:
        model.decode_streams(streams)

    return " ".join(
        stream.result.text.strip() for stream in streams if stream.result.text.strip()
    ).strip()


def _read_audio(path: Path) -> tuple[np.ndarray, int, Path | None]:
    try:
        samples, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    except RuntimeError:
        converted = _convert_to_wav(path)
        samples, sample_rate = sf.read(converted, dtype="float32", always_2d=False)
        return _mono(samples), sample_rate, converted

    if sample_rate == SAMPLE_RATE:
        return _mono(samples), sample_rate, None

    converted = _convert_to_wav(path)
    samples, sample_rate = sf.read(converted, dtype="float32", always_2d=False)
    return _mono(samples), sample_rate, converted


def _mono(samples) -> np.ndarray:
    if getattr(samples, "ndim", 1) > 1:
        samples = samples.mean(axis=1)
    return np.asarray(samples, dtype=np.float32)


def _convert_to_wav(path: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required to convert audio to 16 kHz WAV")

    temp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp.close()
    wav_path = Path(temp.name)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            str(wav_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        try:
            wav_path.unlink()
        except OSError:
            pass
        raise RuntimeError(result.stderr.strip() or "ffmpeg conversion failed")
    return wav_path


def _ensure_model(model_dir: Path) -> None:
    expected = tuple(model_dir / name for name in MODEL_FILES)
    if all(path.is_file() for path in expected):
        return

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_error = None
    with tempfile.TemporaryDirectory(prefix="airtype_sherpa_") as tmpdir:
        archive = Path(tmpdir) / f"{DEFAULT_MODEL}.tar.bz2"
        try:
            urllib.request.urlretrieve(MODEL_URL, archive)
            with tarfile.open(archive, "r:bz2") as tar:
                tar.extractall(model_dir.parent, filter="data")
        except Exception as exc:
            archive_error = exc

    if not all(path.is_file() for path in expected):
        try:
            _download_model_files_from_hf(model_dir)
        except Exception as exc:
            raise RuntimeError(
                f"Could not download {DEFAULT_MODEL} from GitHub or Hugging Face"
            ) from archive_error or exc

    if not all(path.is_file() for path in expected):
        raise RuntimeError(f"Downloaded sherpa model is incomplete: {model_dir}")


def _download_model_files_from_hf(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        url = f"https://huggingface.co/{HF_MODEL_REPO}/resolve/main/{name}"
        target = model_dir / name
        urllib.request.urlretrieve(url, target)


def _prepare_sherpa_onnx_runtime() -> None:
    onnx_spec = find_spec("onnxruntime")
    sherpa_spec = find_spec("sherpa_onnx")
    if onnx_spec is None or sherpa_spec is None or not onnx_spec.origin:
        return

    onnx_lib_dir = Path(onnx_spec.origin).parent / "capi"
    sherpa_dir = Path(sherpa_spec.origin).parent / "lib"
    runtime_libs = sorted(onnx_lib_dir.glob("libonnxruntime.so.*"))
    if not runtime_libs:
        return

    target = sherpa_dir / "libonnxruntime.so"
    source = runtime_libs[-1]
    if target.exists() or target.is_symlink():
        try:
            if target.resolve() == source.resolve():
                return
            target.unlink()
        except OSError:
            return
    try:
        target.symlink_to(source)
    except OSError:
        pass
