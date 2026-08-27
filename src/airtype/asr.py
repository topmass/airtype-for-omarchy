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

from .config import configured_model_dir, load_config
from .registry import ModelSpec, get_model_spec

SAMPLE_RATE = 16000


def active_model_spec() -> ModelSpec:
    return get_model_spec(load_config().get("model"))


def default_model_dir(spec: ModelSpec | None = None) -> Path:
    spec = spec or active_model_spec()
    return configured_model_dir(spec.dir_name)


def model_exists(spec: ModelSpec | None = None, model_dir: Path | None = None) -> bool:
    spec = spec or active_model_spec()
    model_dir = model_dir or default_model_dir(spec)
    return all((model_dir / name).is_file() for name in spec.files.values())


def ensure_model_download(
    spec: ModelSpec | None = None,
    model_dir: Path | None = None,
    progress: bool = False,
) -> Path:
    spec = spec or active_model_spec()
    model_dir = model_dir or default_model_dir(spec)
    _ensure_model(spec, model_dir, progress=progress)
    return model_dir


def _progress_hook(block_num: int, block_size: int, total_size: int) -> None:
    if total_size <= 0:
        return
    done = min(block_num * block_size, total_size)
    percent = done * 100 // total_size
    print(
        f"\r  downloading: {percent:3d}% ({done // (1 << 20)} / {total_size // (1 << 20)} MB)",
        end="",
        flush=True,
    )
    if done >= total_size:
        print()


def load_model():
    spec = active_model_spec()
    model_dir = default_model_dir(spec)
    _ensure_model(spec, model_dir, progress=False)
    prepare_sherpa_onnx_runtime()

    import sherpa_onnx

    threads = int(os.environ.get("AIRTYPE_SHERPA_THREADS", "4"))
    if spec.kind == "moonshine_v2":
        recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
            encoder=str(model_dir / spec.files["encoder"]),
            decoder=str(model_dir / spec.files["decoder"]),
            tokens=str(model_dir / spec.files["tokens"]),
            num_threads=threads,
            provider="cpu",
        )
    else:
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_dir / spec.files["encoder"]),
            decoder=str(model_dir / spec.files["decoder"]),
            joiner=str(model_dir / spec.files["joiner"]),
            tokens=str(model_dir / spec.files["tokens"]),
            num_threads=threads,
            model_type="nemo_transducer",
            provider="cpu",
        )
    return recognizer, f"{spec.name}/cpu/int8/{threads}t"


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


def _ensure_model(spec: ModelSpec, model_dir: Path, progress: bool = False) -> None:
    expected = tuple(model_dir / name for name in spec.files.values())
    if all(path.is_file() for path in expected):
        return

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_error = None
    reporthook = _progress_hook if progress else None
    with tempfile.TemporaryDirectory(prefix="airtype_sherpa_") as tmpdir:
        archive = Path(tmpdir) / f"{spec.dir_name}.tar.bz2"
        try:
            urllib.request.urlretrieve(spec.url, archive, reporthook=reporthook)
            with tarfile.open(archive, "r:bz2") as tar:
                tar.extractall(model_dir.parent, filter="data")
        except Exception as exc:
            archive_error = exc

    if not all(path.is_file() for path in expected):
        try:
            _download_model_files_from_hf(spec, model_dir)
        except Exception as exc:
            raise RuntimeError(
                f"Could not download {spec.dir_name} from GitHub or Hugging Face"
            ) from archive_error or exc

    if not all(path.is_file() for path in expected):
        raise RuntimeError(f"Downloaded sherpa model is incomplete: {model_dir}")


def _download_model_files_from_hf(spec: ModelSpec, model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    for name in spec.files.values():
        url = f"https://huggingface.co/{spec.hf_repo}/resolve/main/{name}"
        target = model_dir / name
        urllib.request.urlretrieve(url, target)


def prepare_sherpa_onnx_runtime() -> None:
    """Symlink the venv's libonnxruntime into sherpa_onnx/lib so the binding loads.

    The pip sherpa-onnx wheel expects a libonnxruntime.so next to its own libs;
    reusing the onnxruntime wheel's copy avoids shipping a second runtime.
    """
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
