# Mounir — Local AI Assistant

A fully local, private voice-capable assistant. No cloud, no subscriptions.
See [`../jarvis_project.md`](../jarvis_project.md) for the full vision and stages.

**Status:** Stage 1 — core text assistant (streaming chat + conversation memory).

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

## Useful env vars

| Variable | Default | Purpose |
|---|---|---|
| `MOUNIR_MODEL` | `mounir` | Which Ollama model to use |
| `MOUNIR_OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint |
| `MOUNIR_THINK` | `false` | Qwen3 thinking mode (slower, smarter) |
| `MOUNIR_MAX_HISTORY` | `20` | Recent messages kept in the prompt |
| `MOUNIR_DATA_DIR` | `~/.mounir` | Where conversations are saved |

## REPL commands

`/reset` · `/save` · `/load` · `/think` · `/exit`

## Layout

```
mounir/
  config.py   all tunables (env-overridable)
  llm.py      streaming Ollama client
  memory.py   conversation history + JSON persistence
  agent.py    orchestration — where Stage 3 tools will hook in
cli.py        text REPL
modelfiles/   mounir.Modelfile
```
