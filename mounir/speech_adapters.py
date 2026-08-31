"""Capability-driven speech adapter registry and normalized speech results.

The registry is the contract shared by persistence, the admin UI, discovery,
connection tests, and runtime dispatch.  Provider-specific wire formats stay in
this module; user-specific endpoints, models, voices, and options stay in the DB.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class SpeechAdapterError(RuntimeError):
    """A provider/runtime failure safe to surface in Agent Studio."""


@dataclass(frozen=True)
class AudioResult:
    data: bytes
    mime_type: str = "audio/wav"
    encoding: str = "wav"
    sample_rate: int | None = None
    channels: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def for_browser(self) -> "AudioResult":
        """Wrap headerless PCM while preserving browser-native compressed audio."""
        mime = self.mime_type.split(";", 1)[0].strip().lower()
        if mime in {"audio/pcm", "audio/l16"} or self.encoding.lower() in {
            "pcm",
            "linear16",
        }:
            return AudioResult(
                self.as_wav(),
                "audio/wav",
                "wav",
                self.sample_rate,
                self.channels,
                self.metadata,
            )
        return self

    def as_wav(self) -> bytes:
        """Return a PCM16 WAV container for legacy playback consumers."""
        if not self.data:
            return b""
        mime = self.mime_type.split(";", 1)[0].strip().lower()
        encoding = self.encoding.lower()
        if self.data[:4] == b"RIFF" or mime in {"audio/wav", "audio/x-wav"}:
            return self.data
        if mime in {"audio/pcm", "audio/l16"} or encoding in {"pcm", "linear16"}:
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(max(1, int(self.channels or 1)))
                wav.setsampwidth(2)
                wav.setframerate(max(8000, int(self.sample_rate or 24000)))
                wav.writeframes(self.data)
            return output.getvalue()
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                    "-f", "wav", "-acodec", "pcm_s16le", "pipe:1",
                ],
                input=self.data,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SpeechAdapterError(
                f"The provider returned {mime or encoding}, but FFmpeg could not decode it: {exc}"
            ) from exc
        if proc.returncode != 0 or not proc.stdout:
            detail = proc.stderr.decode(errors="ignore").strip()[-400:]
            raise SpeechAdapterError(
                f"The provider returned {mime or encoding}, but it could not be converted to WAV"
                + (f": {detail}" if detail else ".")
            )
        return proc.stdout


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    words: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _option(
    key: str,
    label: str,
    field_type: str = "text",
    *,
    default: Any = "",
    hint: str = "",
    choices: list[dict[str, str]] | None = None,
    advanced: bool = True,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "type": field_type,
        "default": default,
        "hint": hint,
        "choices": choices or [],
        "advanced": advanced,
    }


FORMAT_CHOICES = [
    {"value": "auto", "label": "Provider default"},
    {"value": "wav", "label": "WAV"},
    {"value": "mp3", "label": "MP3"},
    {"value": "pcm", "label": "Raw PCM"},
    {"value": "opus", "label": "Opus"},
    {"value": "flac", "label": "FLAC"},
    {"value": "aac", "label": "AAC"},
]


ADAPTERS: dict[str, dict[str, dict[str, Any]]] = {
    "stt": {
        "openai_compatible": {
            "id": "openai_compatible", "label": "OpenAI-compatible transcription",
            "description": "One-shot multipart /audio/transcriptions API used by OpenAI, Groq, OpenRouter, Together, and many local servers.",
            "locations": ["cloud", "local"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "Model ID", "model_required": True,
            "voice": "none", "language": "optional", "discovery": ["models"],
            "options": [
                _option("temperature", "Temperature", "number", default=0, hint="Optional decoding temperature from 0 to 1."),
                _option("prompt", "Recognition prompt", hint="Optional vocabulary or style hint. Some compatible providers ignore it."),
            ],
        },
        "deepgram": {
            "id": "deepgram", "label": "Deepgram native STT",
            "description": "Native /v1/listen request with Deepgram model, language, diarization, and formatting options.",
            "locations": ["cloud", "local"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "Deepgram model", "model_required": True,
            "voice": "none", "language": "optional", "discovery": [],
            "options": [
                _option("smart_format", "Smart formatting", "boolean", default=True),
                _option("punctuate", "Punctuation", "boolean", default=True),
                _option("diarize", "Speaker diarization", "boolean", default=False),
                _option("detect_language", "Detect language", "boolean", default=False),
            ],
        },
        "elevenlabs": {
            "id": "elevenlabs", "label": "ElevenLabs Speech to Text",
            "description": "Native Scribe multipart transcription API with optional diarization and audio tagging.",
            "locations": ["cloud"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "Scribe model ID", "model_required": True,
            "voice": "none", "language": "optional", "discovery": ["models"],
            "options": [
                _option("diarize", "Speaker diarization", "boolean", default=False),
                _option("tag_audio_events", "Tag audio events", "boolean", default=False),
            ],
        },
        "google_stt": {
            "id": "google_stt", "label": "Google Cloud Speech-to-Text",
            "description": "Google synchronous recognition REST API. Use a BCP-47 language such as en-US.",
            "locations": ["cloud"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "Recognition model", "model_required": True,
            "voice": "none", "language": "required", "discovery": [],
            "options": [
                _option("automatic_punctuation", "Automatic punctuation", "boolean", default=True),
                _option("profanity_filter", "Profanity filter", "boolean", default=False),
            ],
        },
        "azure_stt": {
            "id": "azure_stt", "label": "Azure Speech-to-Text",
            "description": "Azure short-audio REST recognition. The selected key is sent as Ocp-Apim-Subscription-Key.",
            "locations": ["cloud"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "Endpoint/model label", "model_required": True,
            "voice": "none", "language": "required", "discovery": [],
            "options": [_option("profanity", "Profanity handling", default="masked", choices=[
                {"value": "masked", "label": "Masked"}, {"value": "removed", "label": "Removed"},
                {"value": "raw", "label": "Raw"},
            ])],
        },
        "aws_transcribe": {
            "id": "aws_transcribe", "label": "Amazon Transcribe Streaming",
            "description": "Amazon Transcribe through the AWS streaming SDK and standard AWS credential chain. A recorded turn is streamed as PCM.",
            "locations": ["cloud"], "connection": "aws", "transport": "AWS streaming SDK",
            "modes": ["one_shot"], "model_label": "Configuration label", "model_required": True,
            "voice": "none", "language": "required", "discovery": [],
            "options": [
                _option("region", "AWS region", hint="Leave blank to use the standard AWS configuration.", advanced=False),
                _option("vocabulary_name", "Custom vocabulary name"),
                _option("show_speaker_label", "Speaker labels", "boolean", default=False),
            ],
        },
        "local_whisper": {
            "id": "local_whisper", "label": "Faster Whisper (in process)",
            "description": "Loads a CTranslate2 Whisper model directly inside Mounir. Model can be a known ID or converted directory.",
            "locations": ["local"], "connection": "none", "transport": "In process",
            "modes": ["one_shot"], "model_label": "Model ID or directory", "model_required": True,
            "voice": "none", "language": "optional", "discovery": [],
            "options": [
                _option("device", "Compute device", default="auto", choices=[
                    {"value": "auto", "label": "Automatic"}, {"value": "cpu", "label": "CPU"},
                    {"value": "cuda", "label": "NVIDIA CUDA"},
                ]),
                _option("compute_type", "Compute type", default="default", choices=[
                    {"value": "default", "label": "Runtime default"}, {"value": "int8", "label": "INT8"},
                    {"value": "float16", "label": "Float16"}, {"value": "float32", "label": "Float32"},
                ]),
                _option("beam_size", "Beam size", "integer", default=1),
                _option("vad_filter", "Voice activity filter", "boolean", default=False),
            ],
        },
        "wyoming": {
            "id": "wyoming", "label": "Wyoming STT service",
            "description": "Local Wyoming protocol service for Whisper, Vosk, Speech-to-Phrase, or another advertised STT engine.",
            "locations": ["local"], "connection": "tcp", "transport": "Wyoming TCP",
            "modes": ["one_shot"], "model_label": "Service/model label", "model_required": True,
            "voice": "none", "language": "optional", "discovery": [], "options": [],
        },
    },
    "tts": {
        "openai_compatible": {
            "id": "openai_compatible", "label": "OpenAI-compatible speech",
            "description": "One-shot /audio/speech API used by OpenAI, OpenRouter, and many self-hosted speech servers.",
            "locations": ["cloud", "local"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "Model ID", "model_required": True,
            "voice": "required", "language": "optional", "discovery": ["models"],
            "options": [
                _option("output_format", "Output format", default="auto", choices=FORMAT_CHOICES, advanced=False),
                _option("sample_rate", "Raw PCM sample rate", "integer", default=24000, hint="Used only when the provider returns headerless PCM."),
                _option("speed", "Speaking speed", "number", default=1),
                _option("instructions", "Voice instructions", hint="Tone or delivery instructions when supported by the selected model."),
            ],
        },
        "deepgram": {
            "id": "deepgram", "label": "Deepgram native TTS",
            "description": "Native Aura (/v1) or Flux (/v2) synthesis. The model family selects the API version automatically unless overridden.",
            "locations": ["cloud", "local"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "Voice model", "model_required": True,
            "voice": "none", "language": "optional", "discovery": [],
            "options": [
                _option("api_version", "Deepgram API version", default="auto", choices=[
                    {"value": "auto", "label": "Automatic from model"},
                    {"value": "v1", "label": "V1 — Aura"},
                    {"value": "v2", "label": "V2 — Flux"},
                ], advanced=False),
                _option("output_format", "Output format", default="auto", choices=FORMAT_CHOICES, advanced=False),
                _option(
                    "sample_rate", "Sample rate", "integer", default=24000,
                    hint="Used only for WAV or raw PCM. Provider-default and compressed audio omit this parameter.",
                ),
                _option("speed", "Speaking speed", "number", default=1),
            ],
        },
        "elevenlabs": {
            "id": "elevenlabs", "label": "ElevenLabs Text to Speech",
            "description": "Native ElevenLabs synthesis using a voice ID in the endpoint and a separate model ID.",
            "locations": ["cloud"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "TTS model ID", "model_required": True,
            "voice": "required", "language": "optional", "discovery": ["models", "voices"],
            "options": [
                _option("output_format", "Output format", default="mp3_44100_128", hint="Exact ElevenLabs output_format value.", advanced=False),
                _option("stability", "Voice stability", "number", default=0.5),
                _option("similarity_boost", "Similarity boost", "number", default=0.75),
            ],
        },
        "google": {
            "id": "google", "label": "Google Cloud Text-to-Speech",
            "description": "Google REST synthesis with voice discovery, language selection, and base64 audio responses.",
            "locations": ["cloud"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "Voice name", "model_required": True,
            "voice": "none", "language": "required", "discovery": ["voices"],
            "options": [
                _option("output_format", "Audio encoding", default="LINEAR16", choices=[
                    {"value": "LINEAR16", "label": "WAV / LINEAR16"}, {"value": "MP3", "label": "MP3"},
                    {"value": "OGG_OPUS", "label": "Ogg Opus"},
                ], advanced=False),
                _option("speaking_rate", "Speaking rate", "number", default=1),
                _option("pitch", "Pitch", "number", default=0),
            ],
        },
        "azure": {
            "id": "azure", "label": "Azure Speech synthesis",
            "description": "Azure SSML synthesis with regional/custom endpoints and Azure output-format headers.",
            "locations": ["cloud"], "connection": "http", "transport": "HTTP",
            "modes": ["one_shot"], "model_label": "Voice name", "model_required": True,
            "voice": "none", "language": "required", "discovery": ["voices"],
            "options": [_option("output_format", "Azure output format", default="riff-24khz-16bit-mono-pcm", hint="Exact X-Microsoft-OutputFormat value.", advanced=False)],
        },
        "aws_polly": {
            "id": "aws_polly", "label": "Amazon Polly",
            "description": "Amazon Polly through the AWS SDK credential chain or a named AWS profile; no base URL is required.",
            "locations": ["cloud"], "connection": "aws", "transport": "AWS SDK",
            "modes": ["one_shot"], "model_label": "Voice ID", "model_required": True,
            "voice": "none", "language": "optional", "discovery": ["voices"],
            "options": [
                _option("region", "AWS region", hint="Leave blank to use the standard AWS configuration.", advanced=False),
                _option("profile", "AWS profile", hint="Leave blank to use the default AWS credential chain."),
                _option("engine", "Polly engine", default="neural", choices=[
                    {"value": "standard", "label": "Standard"}, {"value": "neural", "label": "Neural"},
                    {"value": "long-form", "label": "Long-form"}, {"value": "generative", "label": "Generative"},
                ]),
                _option("output_format", "Output format", default="mp3", choices=[
                    {"value": "mp3", "label": "MP3"}, {"value": "ogg_vorbis", "label": "Ogg Vorbis"},
                    {"value": "pcm", "label": "Raw PCM"},
                ], advanced=False),
                _option("sample_rate", "Sample rate", default="24000"),
            ],
        },
        "piper": {
            "id": "piper", "label": "Piper (in process)",
            "description": "Fast local ONNX speech synthesis. The matching .onnx.json configuration must sit beside the model.",
            "locations": ["local"], "connection": "none", "transport": "In process",
            "modes": ["one_shot"], "model_label": "Piper .onnx model", "model_required": True,
            "voice": "none", "language": "optional", "discovery": [],
            "options": [
                _option("speaker_id", "Speaker ID", "integer", default=0),
                _option("length_scale", "Length scale", "number", default=1),
                _option("noise_scale", "Noise scale", "number", default=0.667),
                _option("noise_w_scale", "Phoneme width noise", "number", default=0.8),
                _option("use_cuda", "Use CUDA", "boolean", default=False),
            ],
        },
        "moss_onnx": {
            "id": "moss_onnx", "label": "MOSS TTS Nano ONNX",
            "description": "Local MOSS ONNX runtime with voices discovered from the selected package manifest.",
            "locations": ["local"], "connection": "none", "transport": "In process",
            "modes": ["one_shot"], "model_label": "MOSS engine directory", "model_required": True,
            "voice": "required", "language": "optional", "discovery": ["voices"], "options": [],
        },
        "wyoming": {
            "id": "wyoming", "label": "Wyoming TTS service",
            "description": "Local Wyoming protocol service, commonly backed by Piper or another voice engine.",
            "locations": ["local"], "connection": "tcp", "transport": "Wyoming TCP",
            "modes": ["one_shot"], "model_label": "Service/model label", "model_required": True,
            "voice": "optional", "language": "optional", "discovery": [], "options": [],
        },
    },
}


ALIASES = {
    "stt": {"local": "local_whisper", "groq": "openai_compatible", "openai": "openai_compatible"},
    "tts": {"local": "piper", "moss": "moss_onnx", "openai": "openai_compatible", "google_cloud": "google"},
}

_FASTER_WHISPER_CACHE: dict[tuple[str, str, str], Any] = {}
_PIPER_CACHE: dict[tuple[str, bool], Any] = {}


def normalize_adapter(kind: str, value: str) -> str:
    normalized_kind = str(kind or "").strip().lower()
    adapter = str(value or "").strip().lower()
    adapter = ALIASES.get(normalized_kind, {}).get(adapter, adapter)
    if normalized_kind not in ADAPTERS or adapter not in ADAPTERS[normalized_kind]:
        raise ValueError(f"{normalized_kind.upper() or 'Speech'} adapter is not supported")
    return adapter


def adapter_spec(kind: str, adapter: str) -> dict[str, Any]:
    normalized = normalize_adapter(kind, adapter)
    return dict(ADAPTERS[kind][normalized])


def adapter_catalog(kind: str | None = None) -> list[dict[str, Any]]:
    kinds = [str(kind).lower()] if kind else ["stt", "tts"]
    result: list[dict[str, Any]] = []
    for current_kind in kinds:
        if current_kind not in ADAPTERS:
            raise ValueError("Speech type must be STT or TTS")
        result.extend({**spec, "kind": current_kind} for spec in ADAPTERS[current_kind].values())
    return result


def normalize_options(kind: str, adapter: str, supplied: Any) -> dict[str, Any]:
    spec = adapter_spec(kind, adapter)
    if supplied in (None, ""):
        raw: dict[str, Any] = {}
    elif isinstance(supplied, str):
        try:
            raw = json.loads(supplied)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Speech options must be valid JSON: {exc.msg}") from exc
    elif isinstance(supplied, dict):
        raw = dict(supplied)
    else:
        raise ValueError("Speech options must be an object")
    allowed = {item["key"]: item for item in spec.get("options", [])}
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ValueError(f"Unsupported {spec['label']} option: {', '.join(unknown)}")
    result: dict[str, Any] = {}
    for key, definition in allowed.items():
        value = raw.get(key, definition.get("default"))
        field_type = definition.get("type")
        try:
            if field_type == "boolean":
                if isinstance(value, str):
                    value = value.strip().lower() in {"1", "true", "yes", "on"}
                else:
                    value = bool(value)
            elif field_type == "integer":
                value = int(value)
            elif field_type == "number":
                value = float(value)
            else:
                value = str(value or "").strip()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{definition['label']} has an invalid value") from exc
        choices = {choice["value"] for choice in definition.get("choices", [])}
        if choices and value not in choices:
            raise ValueError(f"{definition['label']} is not supported")
        result[key] = value
    return result


def _query(url: str, values: dict[str, Any]) -> str:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in values.items() if value not in (None, "")})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _headers(runtime: dict, defaults: dict[str, str] | None = None) -> dict[str, str]:
    return {**(defaults or {}), **{str(k): str(v) for k, v in (runtime.get("headers") or {}).items()}}


def _endpoint(base_url: str, operation: str) -> str:
    from .audio_api import endpoint_url
    return endpoint_url(base_url, operation)


def _versioned_endpoint(base_url: str, version: str, operation: str) -> str:
    """Accept a service root, version root, or complete versioned endpoint."""
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SpeechAdapterError("The speech API URL must start with http:// or https://")
    path = parsed.path.rstrip("/")
    operation_suffix = f"/{operation.strip('/')}"
    if path.endswith(operation_suffix):
        prefix = path[: -len(operation_suffix)].rstrip("/")
        if prefix.endswith(("/v1", "/v2")):
            prefix = prefix[:-2] + version
        path = prefix + operation_suffix
    elif path.endswith(("/v1", "/v2")):
        path = path[:-2] + version + operation_suffix
    else:
        path = f"{path}/{version}{operation_suffix}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def _raise_response(response, label: str) -> None:
    if response.ok:
        return
    detail = ""
    try:
        body = response.json()
        detail = str(body.get("error") or body.get("message") or body)[:600]
    except Exception:
        detail = str(response.text or "").strip()[:600]
    raise SpeechAdapterError(
        f"{label} returned HTTP {response.status_code}" + (f": {detail}" if detail else ".")
    )


def safe_error(exc: Exception, runtime: dict | None = None) -> str:
    """Return a concise provider error without echoing configured credentials."""
    message = str(exc) or exc.__class__.__name__
    secrets = []
    if runtime:
        secrets.append(runtime.get("api_key"))
        secrets.extend((runtime.get("headers") or {}).values())
    for secret in secrets:
        value = str(secret or "")
        if value:
            message = message.replace(value, "[redacted]")
    return message[:800]


def _encoding_from_mime(mime: str, fallback: str = "") -> str:
    normalized = str(mime or "").split(";", 1)[0].lower()
    return {
        "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3",
        "audio/mp3": "mp3", "audio/pcm": "pcm", "audio/l16": "pcm",
        "audio/flac": "flac", "audio/aac": "aac", "audio/ogg": "opus",
    }.get(normalized, fallback or "unknown")


def synthesize(text: str, runtime: dict) -> AudioResult:
    adapter = normalize_adapter("tts", runtime.get("adapter") or runtime.get("provider"))
    options = normalize_options("tts", adapter, runtime.get("provider_options"))
    runtime = {**runtime, "adapter": adapter, "provider_options": options}
    function = globals().get(f"_tts_{adapter}")
    if function is None:
        raise SpeechAdapterError(f"The {adapter} TTS adapter has no one-shot runtime")
    return function(text, runtime)


def transcribe(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    adapter = normalize_adapter("stt", runtime.get("adapter") or runtime.get("provider"))
    options = normalize_options("stt", adapter, runtime.get("provider_options"))
    runtime = {**runtime, "adapter": adapter, "provider_options": options}
    function = globals().get(f"_stt_{adapter}")
    if function is None:
        raise SpeechAdapterError(f"The {adapter} STT adapter has no one-shot runtime")
    return function(wav_bytes, runtime)


def _tts_openai_compatible(text: str, runtime: dict) -> AudioResult:
    import requests
    options = runtime["provider_options"]
    payload: dict[str, Any] = {"model": runtime["model"], "input": text}
    if runtime.get("voice"):
        payload["voice"] = runtime["voice"]
    output_format = options.get("output_format") or "auto"
    if output_format != "auto":
        payload["response_format"] = output_format
    if options.get("speed") not in (None, 1, 1.0):
        payload["speed"] = options["speed"]
    if options.get("instructions"):
        payload["instructions"] = options["instructions"]
    headers = _headers(runtime, {"Accept": "audio/*"})
    if runtime.get("api_key"):
        headers.setdefault("Authorization", f"Bearer {runtime['api_key']}")
    response = requests.post(
        _endpoint(runtime["base_url"], "audio/speech"), headers=headers, json=payload,
        timeout=60,
    )
    _raise_response(response, "Speech provider")
    mime = response.headers.get("Content-Type", "audio/octet-stream")
    return AudioResult(
        response.content, mime, _encoding_from_mime(mime, output_format),
        int(options.get("sample_rate") or 24000), metadata={"generation_id": response.headers.get("X-Generation-Id", "")},
    )


def _stt_openai_compatible(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    import requests
    options = runtime["provider_options"]
    data: dict[str, Any] = {"model": runtime["model"], "response_format": "json"}
    if runtime.get("language") and runtime["language"] != "auto":
        data["language"] = runtime["language"]
    if options.get("temperature") not in (None, 0, 0.0):
        data["temperature"] = options["temperature"]
    if options.get("prompt"):
        data["prompt"] = options["prompt"]
    headers = _headers(runtime, {"Accept": "application/json"})
    if runtime.get("api_key"):
        headers.setdefault("Authorization", f"Bearer {runtime['api_key']}")
    response = requests.post(
        _endpoint(runtime["base_url"], "audio/transcriptions"), headers=headers,
        files={"file": ("audio.wav", io.BytesIO(wav_bytes), "audio/wav")}, data=data,
        timeout=60,
    )
    _raise_response(response, "Transcription provider")
    body = response.json()
    return TranscriptionResult(
        str(body.get("text") or "").strip(), str(body.get("language") or runtime.get("language") or "").replace("auto", ""),
        list(body.get("segments") or []), list(body.get("words") or []),
        {key: value for key, value in body.items() if key not in {"text", "language", "segments", "words"}},
    )


def _tts_google(text: str, runtime: dict) -> AudioResult:
    import requests
    options = runtime["provider_options"]
    language = str(runtime.get("language") or "").strip()
    if not language or language == "auto":
        raise SpeechAdapterError("Google Cloud TTS requires an explicit BCP-47 language code")
    encoding = options.get("output_format") or "LINEAR16"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": language, "name": runtime["model"]},
        "audioConfig": {
            "audioEncoding": encoding, "speakingRate": options.get("speaking_rate", 1),
            "pitch": options.get("pitch", 0),
        },
    }
    headers = _headers(runtime)
    params = {"key": runtime["api_key"]} if runtime.get("api_key") else None
    response = requests.post(_endpoint(runtime["base_url"], "text:synthesize"), headers=headers, params=params, json=payload, timeout=60)
    _raise_response(response, "Google Cloud TTS")
    body = response.json()
    data = base64.b64decode(body.get("audioContent") or "")
    mime = {"LINEAR16": "audio/wav", "MP3": "audio/mpeg", "OGG_OPUS": "audio/ogg"}.get(encoding, "audio/octet-stream")
    return AudioResult(data, mime, _encoding_from_mime(mime, encoding.lower()))


def _stt_google_stt(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    import requests
    language = runtime.get("language") or ""
    if language == "auto":
        raise SpeechAdapterError("Google synchronous STT requires an explicit BCP-47 language code")
    options = runtime["provider_options"]
    config: dict[str, Any] = {
        "encoding": "LINEAR16", "sampleRateHertz": 16000, "languageCode": language,
        "enableAutomaticPunctuation": options.get("automatic_punctuation", True),
        "profanityFilter": options.get("profanity_filter", False),
    }
    if runtime.get("model") and runtime["model"] not in {"default", "_"}:
        config["model"] = runtime["model"]
    payload = {"config": config, "audio": {"content": base64.b64encode(wav_bytes).decode("ascii")}}
    url = _endpoint(runtime["base_url"], "speech:recognize")
    if runtime.get("api_key"):
        url = _query(url, {"key": runtime["api_key"]})
    response = requests.post(url, headers=_headers(runtime), json=payload, timeout=60)
    _raise_response(response, "Google Cloud STT")
    body = response.json()
    results = body.get("results") or []
    text_parts, segments = [], []
    for result in results:
        alternative = (result.get("alternatives") or [{}])[0]
        if alternative.get("transcript"):
            text_parts.append(str(alternative["transcript"]).strip())
            segments.append(alternative)
    return TranscriptionResult(" ".join(text_parts), language, segments=segments, metadata=body.get("metadata") or {})


def _tts_azure(text: str, runtime: dict) -> AudioResult:
    import html
    import requests
    language = str(runtime.get("language") or "").strip()
    if not language or language == "auto":
        raise SpeechAdapterError("Azure Speech synthesis requires an explicit language code")
    output_format = runtime["provider_options"].get("output_format") or "riff-24khz-16bit-mono-pcm"
    ssml = f"<speak version='1.0' xml:lang='{html.escape(language)}'><voice name='{html.escape(runtime['model'])}'>{html.escape(text)}</voice></speak>"
    headers = _headers(runtime, {
        "Content-Type": "application/ssml+xml", "X-Microsoft-OutputFormat": output_format,
        "User-Agent": "Mounir",
    })
    if runtime.get("api_key"):
        headers.setdefault("Ocp-Apim-Subscription-Key", runtime["api_key"])
    url = runtime["base_url"] if runtime["base_url"].rstrip("/").endswith("cognitiveservices/v1") else _endpoint(runtime["base_url"], "cognitiveservices/v1")
    response = requests.post(url, headers=headers, data=ssml.encode("utf-8"), timeout=60)
    _raise_response(response, "Azure Speech synthesis")
    mime = response.headers.get("Content-Type", "audio/wav" if output_format.startswith("riff-") else "audio/mpeg")
    return AudioResult(response.content, mime, _encoding_from_mime(mime))


def _stt_azure_stt(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    import requests
    language = runtime.get("language") or ""
    if language == "auto":
        raise SpeechAdapterError("Azure short-audio STT requires an explicit language code")
    base = runtime["base_url"]
    operation = "speech/recognition/conversation/cognitiveservices/v1"
    url = base if base.rstrip("/").endswith(operation) else _endpoint(base, operation)
    url = _query(url, {"language": language, "format": "detailed", "profanity": runtime["provider_options"].get("profanity", "masked")})
    headers = _headers(runtime, {"Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000", "Accept": "application/json"})
    if runtime.get("api_key"):
        headers.setdefault("Ocp-Apim-Subscription-Key", runtime["api_key"])
    response = requests.post(url, headers=headers, data=wav_bytes, timeout=60)
    _raise_response(response, "Azure Speech transcription")
    body = response.json()
    candidates = body.get("NBest") or []
    text = body.get("DisplayText") or (candidates[0].get("Display") if candidates else "") or ""
    return TranscriptionResult(str(text).strip(), language, metadata=body)


async def _aws_transcribe_async(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    try:
        from amazon_transcribe.client import TranscribeStreamingClient
        from amazon_transcribe.handlers import TranscriptResultStreamHandler
        import boto3
    except ImportError as exc:
        raise SpeechAdapterError(
            "Amazon Transcribe requires amazon-transcribe and boto3. Install the optional voice dependencies."
        ) from exc
    language = runtime.get("language") or ""
    if language == "auto":
        raise SpeechAdapterError("Amazon Transcribe Streaming requires an explicit language code")
    options = runtime["provider_options"]
    region = options.get("region") or boto3.Session().region_name
    if not region:
        raise SpeechAdapterError(
            "Amazon Transcribe requires an AWS region in the model options or standard AWS configuration"
        )
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        rate, width, channels = wav.getframerate(), wav.getsampwidth(), wav.getnchannels()
        audio = wav.readframes(wav.getnframes())
    if width != 2 or channels != 1:
        raise SpeechAdapterError("Amazon Transcribe requires 16-bit mono PCM audio")
    client = TranscribeStreamingClient(region=region)
    kwargs: dict[str, Any] = {
        "language_code": language,
        "media_sample_rate_hz": rate,
        "media_encoding": "pcm",
    }
    if options.get("vocabulary_name"):
        kwargs["vocabulary_name"] = options["vocabulary_name"]
    if options.get("show_speaker_label"):
        kwargs["show_speaker_label"] = True
    stream = await client.start_stream_transcription(**kwargs)
    transcripts: list[str] = []

    class Handler(TranscriptResultStreamHandler):
        async def handle_transcript_event(self, transcript_event):
            for result in transcript_event.transcript.results:
                if result.is_partial:
                    continue
                for alternative in result.alternatives[:1]:
                    if alternative.transcript:
                        transcripts.append(alternative.transcript.strip())

    async def send_audio() -> None:
        for offset in range(0, len(audio), 8192):
            await stream.input_stream.send_audio_event(audio_chunk=audio[offset:offset + 8192])
        await stream.input_stream.end_stream()

    await asyncio.gather(send_audio(), Handler(stream.output_stream).handle_events())
    return TranscriptionResult(" ".join(transcripts).strip(), language)


def _stt_aws_transcribe(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    try:
        return asyncio.run(_aws_transcribe_async(wav_bytes, runtime))
    except SpeechAdapterError:
        raise
    except Exception as exc:
        raise SpeechAdapterError(f"Amazon Transcribe failed: {exc}") from exc


def _tts_deepgram(text: str, runtime: dict) -> AudioResult:
    import requests
    options = runtime["provider_options"]
    output_format = options.get("output_format") or "auto"
    api_version = options.get("api_version") or "auto"
    if api_version == "auto":
        api_version = "v2" if str(runtime["model"]).lower().startswith("flux-") else "v1"
    query: dict[str, Any] = {"model": runtime["model"]}
    linear_output = output_format in {"wav", "pcm"}
    if output_format not in {"auto", "mp3"}:
        query["encoding"] = "linear16" if output_format in {"wav", "pcm"} else output_format
        if output_format == "wav": query["container"] = "wav"
    # Deepgram rejects sample_rate for its default MP3 response and other
    # compressed encodings. It is meaningful only for linear PCM output.
    if linear_output and options.get("sample_rate"):
        query["sample_rate"] = options["sample_rate"]
    if options.get("speed") not in (None, 1, 1.0): query["speed"] = options["speed"]
    headers = _headers(runtime, {"Accept": "audio/*"})
    if runtime.get("api_key"): headers.setdefault("Authorization", f"Token {runtime['api_key']}")
    url = _versioned_endpoint(runtime["base_url"], api_version, "speak")
    response = requests.post(_query(url, query), headers=headers, json={"text": text}, timeout=60)
    _raise_response(response, "Deepgram TTS")
    mime = response.headers.get("Content-Type", "audio/mpeg" if output_format in {"auto", "mp3"} and api_version == "v1" else "audio/pcm")
    return AudioResult(
        response.content,
        mime,
        _encoding_from_mime(mime, output_format),
        int(options.get("sample_rate") or 24000) if linear_output or mime in {"audio/pcm", "audio/l16"} else None,
    )


def _stt_deepgram(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    import requests
    options = runtime["provider_options"]
    query = {"model": runtime["model"], **options}
    if runtime.get("language") and runtime["language"] != "auto": query["language"] = runtime["language"]
    headers = _headers(runtime, {"Content-Type": "audio/wav", "Accept": "application/json"})
    if runtime.get("api_key"): headers.setdefault("Authorization", f"Token {runtime['api_key']}")
    response = requests.post(_query(_versioned_endpoint(runtime["base_url"], "v1", "listen"), query), headers=headers, data=wav_bytes, timeout=60)
    _raise_response(response, "Deepgram STT")
    body = response.json()
    channel = (((body.get("results") or {}).get("channels") or [{}])[0])
    alternative = (channel.get("alternatives") or [{}])[0]
    return TranscriptionResult(
        str(alternative.get("transcript") or "").strip(),
        str((alternative.get("languages") or [runtime.get("language") or ""])[0]).replace("auto", ""),
        words=list(alternative.get("words") or []), metadata=body.get("metadata") or {},
    )


def _tts_elevenlabs(text: str, runtime: dict) -> AudioResult:
    import requests
    voice = str(runtime.get("voice") or "").strip()
    if not voice: raise SpeechAdapterError("ElevenLabs TTS requires a voice ID")
    options = runtime["provider_options"]
    base = runtime["base_url"].rstrip("/")
    if base.endswith("/v1"): url = f"{base}/text-to-speech/{voice}"
    else: url = f"{base}/v1/text-to-speech/{voice}"
    url = _query(url, {"output_format": options.get("output_format")})
    headers = _headers(runtime, {"Accept": "audio/*"})
    if runtime.get("api_key"): headers.setdefault("xi-api-key", runtime["api_key"])
    payload = {
        "text": text, "model_id": runtime["model"],
        "voice_settings": {"stability": options.get("stability", 0.5), "similarity_boost": options.get("similarity_boost", 0.75)},
    }
    if runtime.get("language") and runtime["language"] != "auto": payload["language_code"] = runtime["language"]
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    _raise_response(response, "ElevenLabs TTS")
    output_format = str(options.get("output_format") or "mp3_44100_128")
    if output_format.startswith("pcm_"): fallback_mime = "audio/pcm"
    elif output_format.startswith("wav_"): fallback_mime = "audio/wav"
    elif output_format.startswith("opus_"): fallback_mime = "audio/ogg"
    else: fallback_mime = "audio/mpeg"
    sample_rate = next((int(part) for part in output_format.split("_") if part.isdigit() and int(part) >= 8000), None)
    mime = response.headers.get("Content-Type", fallback_mime)
    return AudioResult(response.content, mime, _encoding_from_mime(mime, output_format.split("_", 1)[0]), sample_rate)


def _stt_elevenlabs(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    import requests
    base = runtime["base_url"].rstrip("/")
    url = f"{base}/speech-to-text" if base.endswith("/v1") else f"{base}/v1/speech-to-text"
    headers = _headers(runtime, {"Accept": "application/json"})
    if runtime.get("api_key"): headers.setdefault("xi-api-key", runtime["api_key"])
    options = runtime["provider_options"]
    data: dict[str, Any] = {"model_id": runtime["model"], "diarize": str(options.get("diarize", False)).lower(), "tag_audio_events": str(options.get("tag_audio_events", False)).lower()}
    if runtime.get("language") and runtime["language"] != "auto": data["language_code"] = runtime["language"]
    response = requests.post(url, headers=headers, files={"file": ("audio.wav", io.BytesIO(wav_bytes), "audio/wav")}, data=data, timeout=60)
    _raise_response(response, "ElevenLabs STT")
    body = response.json()
    return TranscriptionResult(str(body.get("text") or "").strip(), str(body.get("language_code") or ""), words=list(body.get("words") or []), metadata={key: value for key, value in body.items() if key not in {"text", "words"}})


def _tts_aws_polly(text: str, runtime: dict) -> AudioResult:
    try:
        import boto3
    except ImportError as exc:
        raise SpeechAdapterError("Amazon Polly requires boto3. Install the optional voice dependencies.") from exc
    options = runtime["provider_options"]
    try:
        session = boto3.Session(profile_name=options.get("profile") or None, region_name=options.get("region") or None)
        client = session.client("polly")
        request: dict[str, Any] = {
            "Text": text, "VoiceId": runtime["model"], "Engine": options.get("engine") or "neural",
            "OutputFormat": options.get("output_format") or "mp3", "SampleRate": str(options.get("sample_rate") or "24000"),
        }
        if runtime.get("language") and runtime["language"] != "auto": request["LanguageCode"] = runtime["language"]
        response = client.synthesize_speech(**request)
        data = response["AudioStream"].read()
    except Exception as exc:
        raise SpeechAdapterError(f"Amazon Polly failed: {exc}") from exc
    mime = response.get("ContentType") or "audio/mpeg"
    return AudioResult(data, mime, _encoding_from_mime(mime, request["OutputFormat"]), int(request["SampleRate"]))


def _tts_piper(text: str, runtime: dict) -> AudioResult:
    from piper import PiperVoice
    options = runtime["provider_options"]
    model_path = Path(runtime["model"]).expanduser()
    if not model_path.is_file(): raise SpeechAdapterError(f"Piper model not found at {model_path}")
    try:
        cache_key = (str(model_path.resolve()), bool(options.get("use_cuda", False)))
        voice = _PIPER_CACHE.get(cache_key)
        if voice is None:
            voice = PiperVoice.load(str(model_path), use_cuda=cache_key[1])
            _PIPER_CACHE[cache_key] = voice
        kwargs = {
            "speaker_id": options.get("speaker_id") or None,
            "length_scale": options.get("length_scale", 1),
            "noise_scale": options.get("noise_scale", 0.667),
            "noise_w_scale": options.get("noise_w_scale", 0.8),
        }
        parts, sample_rate = [], 22050
        for chunk in voice.synthesize(text, **kwargs):
            parts.append(chunk.audio_int16_bytes); sample_rate = chunk.sample_rate
    except Exception as exc:
        raise SpeechAdapterError(f"Piper synthesis failed: {exc}") from exc
    raw = b"".join(parts)
    return AudioResult(raw, "audio/pcm", "pcm", sample_rate)


def _tts_moss_onnx(text: str, runtime: dict) -> AudioResult:
    from . import moss_tts
    voice = str(runtime.get("voice") or "").strip()
    if not voice: raise SpeechAdapterError("The selected MOSS configuration has no voice identifier")
    return AudioResult(moss_tts.synthesize_wav(text, engine_path=runtime["model"], voice=voice))


def _stt_local_whisper(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    try:
        import numpy as np
        import soundfile as sf
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SpeechAdapterError("Faster Whisper requires the optional voice dependencies") from exc
    options = runtime["provider_options"]
    device = options.get("device") or "auto"
    compute_type = options.get("compute_type") or "default"
    try:
        audio, _ = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if getattr(audio, "ndim", 1) > 1: audio = np.asarray(audio).mean(axis=1)
        cache_key = (str(runtime["model"]), str(device), str(compute_type))
        model = _FASTER_WHISPER_CACHE.get(cache_key)
        if model is None:
            model = WhisperModel(runtime["model"], device=device, compute_type=compute_type)
            _FASTER_WHISPER_CACHE[cache_key] = model
        segments_iter, info = model.transcribe(
            audio, language=None if runtime.get("language") in {None, "", "auto"} else runtime["language"],
            beam_size=int(options.get("beam_size") or 1), vad_filter=bool(options.get("vad_filter", False)),
        )
        segments = [{"text": segment.text.strip(), "start": segment.start, "end": segment.end} for segment in segments_iter]
    except Exception as exc:
        raise SpeechAdapterError(f"Faster Whisper transcription failed: {exc}") from exc
    return TranscriptionResult(" ".join(item["text"] for item in segments).strip(), str(info.language or ""), segments=segments)


def _wyoming_address(value: str) -> tuple[str, int]:
    parsed = urlsplit(value if "://" in value else f"tcp://{value}")
    if not parsed.hostname or not parsed.port:
        raise SpeechAdapterError("Wyoming connection must include a host and port, for example tcp://127.0.0.1:10200")
    return parsed.hostname, parsed.port


async def _wyoming_tts_async(text: str, runtime: dict) -> AudioResult:
    try:
        from wyoming.audio import AudioChunk, AudioStart, AudioStop
        from wyoming.client import AsyncTcpClient
        from wyoming.tts import Synthesize
    except ImportError as exc:
        raise SpeechAdapterError("Wyoming support requires the optional 'wyoming' Python package") from exc
    host, port = _wyoming_address(runtime["base_url"])
    chunks, rate, channels = [], 22050, 1
    async with AsyncTcpClient(host, port) as client:
        await client.write_event(Synthesize(text=text, voice=runtime.get("voice") or None, language=runtime.get("language") or None).event())
        while True:
            event = await client.read_event()
            if event is None: break
            if AudioStart.is_type(event.type):
                start = AudioStart.from_event(event); rate, channels = start.rate, start.channels
            elif AudioChunk.is_type(event.type): chunks.append(AudioChunk.from_event(event).audio)
            elif AudioStop.is_type(event.type): break
    return AudioResult(b"".join(chunks), "audio/pcm", "pcm", rate, channels)


def _tts_wyoming(text: str, runtime: dict) -> AudioResult:
    return asyncio.run(_wyoming_tts_async(text, runtime))


async def _wyoming_stt_async(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    try:
        from wyoming.asr import Transcribe, Transcript
        from wyoming.audio import AudioChunk, AudioStart, AudioStop
        from wyoming.client import AsyncTcpClient
    except ImportError as exc:
        raise SpeechAdapterError("Wyoming support requires the optional 'wyoming' Python package") from exc
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        rate, width, channels, audio = wav.getframerate(), wav.getsampwidth(), wav.getnchannels(), wav.readframes(wav.getnframes())
    host, port = _wyoming_address(runtime["base_url"])
    async with AsyncTcpClient(host, port) as client:
        await client.write_event(Transcribe(name=runtime.get("model") or None, language=None if runtime.get("language") == "auto" else runtime.get("language")).event())
        await client.write_event(AudioStart(rate=rate, width=width, channels=channels).event())
        for offset in range(0, len(audio), 4096): await client.write_event(AudioChunk(rate=rate, width=width, channels=channels, audio=audio[offset:offset + 4096]).event())
        await client.write_event(AudioStop().event())
        while True:
            event = await client.read_event()
            if event is None: break
            if Transcript.is_type(event.type):
                transcript = Transcript.from_event(event)
                return TranscriptionResult(transcript.text.strip(), transcript.language or "")
    return TranscriptionResult("")


def _stt_wyoming(wav_bytes: bytes, runtime: dict) -> TranscriptionResult:
    return asyncio.run(_wyoming_stt_async(wav_bytes, runtime))


def discover(kind: str, runtime: dict, target: str) -> list[dict[str, str]]:
    adapter = normalize_adapter(kind, runtime.get("adapter") or runtime.get("provider"))
    if target not in adapter_spec(kind, adapter).get("discovery", []): return []
    if adapter == "moss_onnx" and target == "voices":
        from . import moss_tts
        return moss_tts.list_builtin_voices(runtime["model"])
    if adapter == "piper" and target == "voices":
        path = Path(runtime["model"]).expanduser()
        root = path if path.is_dir() else path.parent
        return [{"id": str(item), "label": item.stem} for item in sorted(root.glob("*.onnx"))]
    if adapter == "aws_polly" and target == "voices":
        try:
            import boto3
            options = normalize_options("tts", adapter, runtime.get("provider_options"))
            session = boto3.Session(profile_name=options.get("profile") or None, region_name=options.get("region") or None)
            voices = session.client("polly").describe_voices().get("Voices") or []
            return [{"id": item["Id"], "label": f"{item['Id']} — {item.get('LanguageName', '')}".strip(" —")} for item in voices]
        except Exception as exc:
            raise SpeechAdapterError(f"Could not discover Amazon Polly voices: {exc}") from exc
    import requests
    headers = _headers(runtime, {"Accept": "application/json"})
    if adapter == "openai_compatible":
        if runtime.get("api_key"): headers.setdefault("Authorization", f"Bearer {runtime['api_key']}")
        response = requests.get(_endpoint(runtime["base_url"], "models"), headers=headers, timeout=20)
        _raise_response(response, "Model discovery")
        data = response.json().get("data") or []
        def supports_speech(item: dict) -> bool:
            architecture = item.get("architecture") or {}
            inputs = item.get("input_modalities") or architecture.get("input_modalities") or []
            outputs = item.get("output_modalities") or architecture.get("output_modalities") or []
            if kind == "tts":
                return not outputs or bool({"audio", "speech"} & set(outputs))
            return not inputs or bool({"audio", "speech"} & set(inputs))

        filtered = [item for item in data if supports_speech(item)]
        return [{"id": str(item.get("id")), "label": str(item.get("name") or item.get("id"))} for item in filtered if item.get("id")]
    if adapter == "google" and target == "voices":
        url = _endpoint(runtime["base_url"], "voices")
        if runtime.get("api_key"): url = _query(url, {"key": runtime["api_key"]})
        response = requests.get(url, headers=headers, timeout=20); _raise_response(response, "Google voice discovery")
        return [{"id": item["name"], "label": f"{item['name']} — {', '.join(item.get('languageCodes') or [])}"} for item in response.json().get("voices") or []]
    if adapter == "azure" and target == "voices":
        if runtime.get("api_key"): headers.setdefault("Ocp-Apim-Subscription-Key", runtime["api_key"])
        base = runtime["base_url"]; operation = "cognitiveservices/voices/list"
        url = base if base.rstrip("/").endswith(operation) else _endpoint(base, operation)
        response = requests.get(url, headers=headers, timeout=20); _raise_response(response, "Azure voice discovery")
        return [{"id": item["ShortName"], "label": f"{item['ShortName']} — {item.get('LocaleName', item.get('Locale', ''))}"} for item in response.json()]
    if adapter == "elevenlabs":
        if runtime.get("api_key"): headers.setdefault("xi-api-key", runtime["api_key"])
        base = runtime["base_url"].rstrip("/")
        operation = "voices" if target == "voices" else "models"
        url = f"{base}/{operation}" if base.endswith("/v1") else f"{base}/v1/{operation}"
        response = requests.get(url, headers=headers, timeout=20); _raise_response(response, "ElevenLabs discovery")
        body = response.json(); items = body.get(operation) if isinstance(body, dict) else body
        id_key = "voice_id" if target == "voices" else "model_id"
        return [{"id": item[id_key], "label": str(item.get("name") or item[id_key])} for item in items or [] if item.get(id_key)]
    return []
