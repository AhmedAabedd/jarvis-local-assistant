"""Lightweight adapter for the official MOSS-TTS-Nano ONNX CPU runtime."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
import threading
import wave
from pathlib import Path

_engine = None
_engine_path: Path | None = None
_engine_lock = threading.Lock()
_synthesis_lock = threading.Lock()

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?\u061f\u061b])\s+")
_MANIFEST_NAME = "browser_poc_manifest.json"
_MANIFEST_RELATIVE_PATHS = (
    Path("models") / _MANIFEST_NAME,
    Path("models/MOSS-TTS-Nano-100M-ONNX") / _MANIFEST_NAME,
    Path("models/MOSS-TTS-Nano-ONNX-CPU") / _MANIFEST_NAME,
    Path(_MANIFEST_NAME),
    Path("MOSS-TTS-Nano-100M-ONNX") / _MANIFEST_NAME,
    Path("MOSS-TTS-Nano-ONNX-CPU") / _MANIFEST_NAME,
)
_MODEL_DIR_ALIASES = {
    "MOSS-TTS-Nano-ONNX-CPU": "MOSS-TTS-Nano-100M-ONNX",
    "MOSS-Audio-Tokenizer-Nano-ONNX-CPU": "MOSS-Audio-Tokenizer-Nano-ONNX",
}


def _find_manifest(engine_path: str) -> Path:
    """Locate a MOSS manifest without assuming a downloaded repository name."""
    root = Path(engine_path).expanduser().resolve()
    for relative_path in _MANIFEST_RELATIVE_PATHS:
        candidate = root / relative_path
        if candidate.is_file():
            return candidate

    discovered: list[Path] = []
    for parent in (root, root / "models"):
        if not parent.is_dir():
            continue
        discovered.extend(
            candidate
            for candidate in parent.glob(f"*/{_MANIFEST_NAME}")
            if candidate.is_file()
        )
    unique = sorted(set(discovered))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        raise ValueError(
            "multiple MOSS voice manifests were found; select the specific model directory"
        )
    raise FileNotFoundError(f"MOSS voice manifest not found under {root}")


def _resolve_package_file(manifest_dir: Path, relative_path: object) -> Path:
    relative = Path(str(relative_path or ""))
    candidate = (manifest_dir / relative).resolve()
    if candidate.is_file():
        return candidate
    relative_text = str(relative).replace("\\", "/")
    for old_name, current_name in _MODEL_DIR_ALIASES.items():
        rewritten = relative_text.replace(old_name, current_name)
        candidate = (manifest_dir / rewritten).resolve()
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"required MOSS model file not found: {relative_path}")


def _validate_model_metadata(metadata_path: Path) -> None:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    files = metadata.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError(f"invalid MOSS model metadata: {metadata_path}")
    for relative_path in files.values():
        _resolve_package_file(metadata_path.parent, relative_path)
    external_files = metadata.get("external_data_files", {})
    if isinstance(external_files, dict):
        for paths in external_files.values():
            if isinstance(paths, list):
                for relative_path in paths:
                    _resolve_package_file(metadata_path.parent, relative_path)


def _validate_package(engine_path: str, manifest_path: Path, manifest: dict) -> None:
    root = Path(engine_path).expanduser().resolve()
    if not (root / "ort_cpu_runtime.py").is_file():
        raise FileNotFoundError(
            f"MOSS ONNX runtime not found at {root / 'ort_cpu_runtime.py'}"
        )
    model_files = manifest.get("model_files")
    if not isinstance(model_files, dict):
        raise ValueError("the MOSS manifest has no model_files section")
    tokenizer = _resolve_package_file(
        manifest_path.parent, model_files.get("tokenizer_model")
    )
    if tokenizer.stat().st_size == 0:
        raise ValueError(f"MOSS tokenizer is empty: {tokenizer}")
    for key in ("tts_meta", "codec_meta"):
        metadata_path = _resolve_package_file(manifest_path.parent, model_files.get(key))
        _validate_model_metadata(metadata_path)


def list_builtin_voices(engine_path: str) -> list[dict[str, str]]:
    """Read voice metadata from the selected package instead of a fixed catalog."""
    manifest_path = _find_manifest(engine_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_package(engine_path, manifest_path, manifest)
    raw_voices = manifest.get("builtin_voices")
    if not isinstance(raw_voices, list):
        return []

    voices: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_voice in raw_voices:
        if not isinstance(raw_voice, dict):
            continue
        voice_id = str(raw_voice.get("voice") or "").strip()
        identity = voice_id.casefold()
        if not voice_id or identity in seen:
            continue
        seen.add(identity)
        voices.append(
            {
                "id": voice_id,
                "label": str(raw_voice.get("display_name") or voice_id).strip(),
                "group": str(raw_voice.get("group") or "").strip(),
            }
        )
    return voices


class MossOnnxEngine:
    """Keep the compact ONNX model loaded and return synthesized WAV bytes."""

    def __init__(self, engine_path: str) -> None:
        import numpy as np
        import sentencepiece as spm

        self.np = np
        self.root = Path(engine_path).expanduser().resolve()
        runtime_path = self.root / "ort_cpu_runtime.py"
        if not runtime_path.is_file():
            raise FileNotFoundError(
                f"MOSS-TTS-Nano runtime not found at {runtime_path}"
            )
        module_name = "_mounir_moss_ort_cpu_runtime"
        spec = importlib.util.spec_from_file_location(module_name, runtime_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load the MOSS-TTS-Nano ONNX runtime")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        self.runtime = module.OrtCpuRuntime(
            model_dir=self.root / "models",
            thread_count=min(4, os.cpu_count() or 1),
            sample_mode=module.SAMPLE_MODE_FIXED,
        )
        tokenizer_path = self.runtime.resolve_manifest_relative_path(
            self.runtime.manifest["model_files"].get(
                "tokenizer_model", "tokenizer.model"
            )
        )
        self.tokenizer = spm.SentencePieceProcessor(model_file=str(tokenizer_path))
        self.sample_rate = int(
            self.runtime.codec_meta["codec_config"]["sample_rate"]
        )
        self.channels = int(self.runtime.codec_meta["codec_config"]["channels"])

    def _token_ids(self, text: str) -> list[int]:
        return [int(value) for value in self.tokenizer.encode(text, out_type=int)]

    def _split_text(self, text: str, max_tokens: int = 75) -> list[str]:
        sentences = [
            part.strip() for part in _SENTENCE_BOUNDARY.split(text) if part.strip()
        ]
        chunks: list[str] = []
        current = ""
        for sentence in sentences or [text]:
            candidate = f"{current} {sentence}".strip()
            if current and len(self._token_ids(candidate)) > max_tokens:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
            while len(self._token_ids(current)) > max_tokens:
                words = current.split()
                cut = max(1, len(words) // 2)
                while (
                    cut > 1
                    and len(self._token_ids(" ".join(words[:cut]))) > max_tokens
                ):
                    cut -= 1
                chunks.append(" ".join(words[:cut]))
                current = " ".join(words[cut:])
        if current:
            chunks.append(current)
        return chunks

    def _decode(self, frames: list[list[int]]):
        try:
            channel_arrays, audio_length = self.runtime.decode_full_audio(frames)
            return self.np.stack(
                [channel[:audio_length] for channel in channel_arrays], axis=1
            )
        except Exception:
            session = self.runtime.codec_streaming_session
            by_channel: list[list] = [[] for _ in range(self.channels)]
            session.reset()
            try:
                for start in range(0, len(frames), 8):
                    decoded = session.run_frames(frames[start : start + 8])
                    if decoded is None:
                        continue
                    audio, audio_length = decoded
                    for index, channel in enumerate(audio[0, :, :audio_length]):
                        by_channel[index].append(
                            self.np.asarray(channel, dtype=self.np.float32)
                        )
            finally:
                session.reset()
            return self.np.stack(
                [
                    self.np.concatenate(parts)
                    if parts
                    else self.np.zeros((0,), dtype=self.np.float32)
                    for parts in by_channel
                ],
                axis=1,
            )

    def synthesize_wav(self, text: str, voice: str) -> bytes:
        voices = self.runtime.list_builtin_voices()
        selected = next(
            (item for item in voices if item["voice"].lower() == voice.lower()),
            None,
        )
        if selected is None:
            available = ", ".join(item["voice"] for item in voices)
            raise ValueError(
                f"MOSS voice '{voice}' is unavailable; choose one of: {available}"
            )

        waveforms = []
        chunks = self._split_text(text)
        for chunk in chunks:
            request = self.runtime.build_voice_clone_request_rows(
                selected["prompt_audio_codes"], self._token_ids(chunk)
            )
            waveforms.append(self._decode(self.runtime.generate_audio_frames(request)))
        waveform = self.np.concatenate(waveforms, axis=0)
        pcm = (
            self.np.clip(waveform, -1.0, 1.0) * 32767.0
        ).astype(self.np.int16)
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return output.getvalue()


def synthesize_wav(text: str, *, engine_path: str, voice: str) -> bytes:
    global _engine, _engine_path
    resolved = Path(engine_path).expanduser().resolve()
    with _engine_lock:
        if _engine is None or _engine_path != resolved:
            _engine = MossOnnxEngine(str(resolved))
            _engine_path = resolved
    with _synthesis_lock:
        return _engine.synthesize_wav(text, voice)
