# Speech integration architecture

Mounir treats speech universality as a capability contract, not as a claim that every
speech model accepts the same request. The common application contract is normalized;
provider-specific wire formats remain isolated in adapters.

## The vocabulary used in the interface

| Term | Meaning | Examples |
|---|---|---|
| Capability | What the model does | Text to speech (TTS), speech to text (STT) |
| Adapter | The API or runtime contract Mounir speaks | OpenAI-compatible, Azure Speech, Piper |
| Provider | A saved connection and credential | A user's OpenRouter account or local server |
| Model | The provider/runtime model identifier | `deepgram/flux-tts:free`, `whisper-1` |
| Voice | A speaker identity or preset, when applicable | `flux-alexis-en`, an ElevenLabs voice ID |
| Location | Where computation runs | Cloud or local |
| Mode | How a speech turn is transported | One-shot, realtime streaming, asynchronous job |

Location and adapter are deliberately independent. A local server may implement the
OpenAI-compatible adapter, and a remotely hosted service may implement Wyoming or a
private native API. “Local” is not an API format.

## Implemented adapter matrix

| Capability | Adapter | Connection | Current mode | Discovery |
|---|---|---|---|---|
| STT | OpenAI-compatible | HTTP multipart | One-shot | Models when `/models` exists |
| STT | Deepgram | Native HTTP | One-shot | Manual model ID |
| STT | ElevenLabs | Native HTTP multipart | One-shot | Models |
| STT | Google Cloud Speech | Native JSON REST | One-shot | Manual model/language |
| STT | Azure Speech | Native audio REST | One-shot short audio | Manual model/language |
| STT | Amazon Transcribe | AWS streaming SDK | Recorded turn streamed to service | Manual configuration |
| STT | Faster Whisper | In-process CTranslate2 | One-shot | Runtime-supported model/path |
| STT | Wyoming | Local TCP protocol | One-shot | Service-defined |
| TTS | OpenAI-compatible | HTTP JSON | One-shot | Models when `/models` exists |
| TTS | Deepgram | Native HTTP | One-shot | Manual voice model |
| TTS | ElevenLabs | Native HTTP | One-shot | Models and voices |
| TTS | Google Cloud TTS | Native JSON REST | One-shot | Voices |
| TTS | Azure Speech | SSML over native REST | One-shot | Voices |
| TTS | Amazon Polly | AWS SDK | One-shot | Voices |
| TTS | Piper | In-process | One-shot | Selected local ONNX voice |
| TTS | MOSS-TTS-Nano ONNX | In-process | One-shot | Package manifest voices |
| TTS | Wyoming | Local TCP protocol | One-shot | Service-defined |

Only the modes in this table are advertised by the API and UI. Native realtime
WebSocket sessions, bidirectional interruption, and asynchronous long-audio jobs need
different lifecycle contracts and are intentionally not simulated by the one-shot
pipeline.

## OpenAI-compatible behavior

The adapter accepts an API root or the complete operation endpoint and appends
`/audio/speech` or `/audio/transcriptions` only when needed. It supports optional
Bearer authentication and provider-defined HTTP headers stored with the provider.

For TTS, `output_format = auto` omits `response_format`. This is important for
compatible gateways such as OpenRouter and for servers whose supported output differs
from OpenAI's defaults. Mounir preserves the response `Content-Type` and bytes. Raw PCM
stores its sample rate and channel count and can be wrapped in a WAV container;
compressed formats are converted only for consumers that explicitly require WAV.

Compatibility is still behavioral: a service calling itself OpenAI-compatible may
support only a subset of models, fields, formats, or discovery. The connection test
therefore sends a real minimal request instead of declaring compatibility from the URL.

## Configuration and secrets

Adapter capabilities define which fields the interface displays and validates. User
choices are stored in SQLite and reload unchanged. No provider model, voice, language,
path, or endpoint is added to a selection list unless it is a protocol-level option or
was returned by discovery.

API keys may be literal values or environment-variable references supported by the
existing provider system. Provider headers are intended for routing and attribution,
for example OpenRouter's optional `HTTP-Referer` and `X-OpenRouter-Title`; credentials
should remain in the API-key field. AWS adapters use the standard AWS credential chain.

## Local runtime boundaries

In-process adapters load the model family they explicitly name:

- Faster Whisper loads compatible CTranslate2 Whisper models.
- Piper loads a Piper ONNX voice and matching configuration.
- MOSS loads the selected MOSS-TTS-Nano ONNX runtime and manifest voice.

Other local engines should normally expose an OpenAI-compatible HTTP service or a
Wyoming service. This keeps process management and model-specific dependencies outside
the core application while preserving the same saved-provider and testing experience.

## Adding another adapter

A new integration belongs in the speech adapter registry and must declare its
locations, connection type, mode, model/voice/language requirements, discoverable
resources, and provider-specific options. It must return `AudioResult` or
`TranscriptionResult`, expose failures rather than silently falling back, and include
a real connection test. This metadata drives validation and the interface, preventing
the backend and frontend support lists from drifting apart.
