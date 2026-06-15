# Mounir — Local AI Assistant

A fully local, private voice-capable assistant. No cloud, no subscriptions.
See [`../jarvis_project.md`](../jarvis_project.md) for the full vision and stages.

**Status:** Stage 2 (in progress) — voice: push-to-talk, Whisper STT, Piper TTS.
Stage 1 (streaming text chat + memory) is on `main`.

## Target hardware (DELL / stage01)

Intel i5-8400 · 16 GB RAM · Intel UHD 630 · Ubuntu 22.04 · CPU-only inference.

Daily-driver model is **Qwen3 4B Q4** for speed; 8B is kept for hard tasks.

## Setup (on the DELL)

```bash
# 1. Pull the base model
ollama pull qwen3:4b

# 2. Build the custom 'mounir' model (personality + params)
ollama create mounir -f modelfiles/mounir.Modelfile

# 3. Python deps
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. Talk to it
python cli.py
```

If you'd rather skip the custom build, run against the base model directly:

```bash
MOUNIR_MODEL=qwen3:4b python cli.py   # personality injected from config.py
```

## Voice mode (Stage 2)

```bash
# System audio lib
sudo apt install portaudio19-dev

# Python deps (on top of requirements.txt)
pip install -r requirements-voice.txt

# Download a Piper voice (English example) into ~/.mounir/voices/
mkdir -p ~/.mounir/voices && cd ~/.mounir/voices
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json

# Talk to Mounir
python voice_cli.py
```

Press Enter to start talking, Enter again to stop. Whisper transcribes, Qwen3
replies, Piper speaks each sentence as soon as it's ready. For Arabic TTS, grab
an `ar_*` voice from the same repo and set `MOUNIR_PIPER_MODEL` to its path.

## Useful env vars

| Variable | Default | Purpose |
|---|---|---|
| `MOUNIR_MODEL` | `mounir` | Which Ollama model to use |
| `MOUNIR_OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint |
| `MOUNIR_THINK` | `false` | Qwen3 thinking mode (slower, smarter) |
| `MOUNIR_MAX_HISTORY` | `20` | Recent messages kept in the prompt |
| `MOUNIR_DATA_DIR` | `~/.mounir` | Where conversations are saved |
| `MOUNIR_WHISPER_MODEL` | `small` | Whisper size (`base` for more speed) |
| `MOUNIR_WHISPER_LANG` | auto | Force STT language, e.g. `en` or `ar` |
| `MOUNIR_PIPER_MODEL` | `~/.mounir/voices/en_US-amy-medium.onnx` | TTS voice file |

## REPL commands

`/reset` · `/save` · `/load` · `/think` · `/exit`

## Layout

```
mounir/
  config.py     all tunables (env-overridable)
  llm.py        streaming Ollama client
  memory.py     conversation history + JSON persistence
  agent.py      orchestration — where Stage 3 tools will hook in
  sentences.py  stream → sentence splitter (for speak-as-you-go)
  audio.py      microphone capture (push-to-talk)
  stt.py        speech-to-text (faster-whisper)
  tts.py        text-to-speech (Piper)
  voice.py      the voice loop
cli.py          text REPL
voice_cli.py    voice entry point
modelfiles/     mounir.Modelfile
```
