"""Registry of supported sherpa-onnx ASR models."""

from dataclasses import dataclass

# Verified against the k2-fsa asr-models release page and HF mirrors (2026-08).
GITHUB_RELEASE_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"

TRANSDUCER_FILES = {
    "encoder": "encoder.int8.onnx",
    "decoder": "decoder.int8.onnx",
    "joiner": "joiner.int8.onnx",
    "tokens": "tokens.txt",
}


@dataclass(frozen=True)
class ModelSpec:
    name: str  # config key, e.g. "parakeet-unified-en-0.6b-int8"
    kind: str  # "transducer" | "moonshine_v2"
    dir_name: str  # extracted directory name == tarball base name
    files: dict[str, str]  # role -> filename inside dir
    hf_repo: str  # per-file download fallback
    display: str
    size_label: str  # approximate download size shown before download
    langs: str

    @property
    def url(self) -> str:
        return f"{GITHUB_RELEASE_BASE}/{self.dir_name}.tar.bz2"


MODEL_REGISTRY: dict[str, ModelSpec] = {
    spec.name: spec
    for spec in (
        ModelSpec(
            name="parakeet-unified-en-0.6b-int8",
            kind="transducer",
            dir_name="sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming",
            files=TRANSDUCER_FILES,
            hf_repo="csukuangfj2/sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming",
            display="Parakeet Unified EN 0.6B (default)",
            size_label="about 480 MB download, 660 MB on disk",
            langs="English, punctuation + capitalization",
        ),
        ModelSpec(
            name="parakeet-tdt-0.6b-v3-int8",
            kind="transducer",
            dir_name="sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
            files=TRANSDUCER_FILES,
            hf_repo="csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
            display="Parakeet TDT 0.6B v3 (multilingual)",
            size_label="about 465 MB download, 670 MB on disk",
            langs="25 European languages",
        ),
        ModelSpec(
            name="parakeet-tdt-0.6b-v2-int8",
            kind="transducer",
            dir_name="sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
            files=TRANSDUCER_FILES,
            hf_repo="csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
            display="Parakeet TDT 0.6B v2 (legacy)",
            size_label="about 600 MB download",
            langs="English, no punctuation",
        ),
        ModelSpec(
            name="moonshine-base-en",
            kind="moonshine_v2",
            dir_name="sherpa-onnx-moonshine-base-en-quantized-2026-02-27",
            files={
                "encoder": "encoder_model.ort",
                "decoder": "decoder_model_merged.ort",
                "tokens": "tokens.txt",
            },
            hf_repo="csukuangfj2/sherpa-onnx-moonshine-base-en-quantized-2026-02-27",
            display="Moonshine Base EN (light)",
            size_label="about 110 MB download, 145 MB on disk",
            langs="English, lower accuracy, weak hardware",
        ),
    )
}

DEFAULT_MODEL_KEY = "parakeet-unified-en-0.6b-int8"


def get_model_spec(name: str | None) -> ModelSpec:
    if name and name in MODEL_REGISTRY:
        return MODEL_REGISTRY[name]
    return MODEL_REGISTRY[DEFAULT_MODEL_KEY]
