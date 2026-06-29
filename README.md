# Mounir — a local, private AI assistant

Mounir is a Jarvis-style assistant that runs on **your own machine**. The brain
that talks to you is a **local LLM** (via [Ollama](https://ollama.com)); it can
read and edit files, run shell commands, open apps, send email, read media
(images, PDFs, audio, video), and — when a job needs more horsepower — hand off
to specialist agents in the cloud. No data leaves your box unless a tool
explicitly sends it.

It speaks too: optional voice mode transcribes your speech (Whisper), thinks, and
talks back (Piper). There's also a small web dashboard.

> Personal project. Sharp, no-fluff personality by design.

---

## How it works — a LangGraph multi-agent

Mounir is built as a **supervisor + specialists** graph
([LangGraph](https://github.com/langchain-ai/langgraph)). The supervisor is the
assistant you talk to; it does general work itself and **delegates** the heavy,
context-hungry jobs to isolated specialist agents, getting back only a compact
report.

```
                       ┌──────────────────┐
   you ── text ──────▶ │    SUPERVISOR    │ ── answer ──▶ you
                       │   (local LLM)    │
                       │  files · bash ·  │
                       │  browser · email │
                       └─┬───────┬──────┬─┘
        delegate_to_     │       │      │     delegate_to_media
        researcher       │       │      │
                         ▼       ▼      ▼
              ┌────────────┐ ┌────────┐ ┌────────────┐
              │ RESEARCHER │ │ CODER  │ │   MEDIA    │
              │  (NVIDIA)  │ │(NVIDIA)│ │  (NVIDIA)  │
              │ web search │ │ file   │ │ img · pdf  │
              │ fetch page │ │ edits  │ │ audio·vid  │
              └─────┬──────┘ └───┬────┘ └─────┬──────┘
                    └──────── report back ────┘
```

> The supervisor reaches the **researcher** and **media** specialists by tool
> call. The **coder** node is wired the same way but its `delegate_to_coder`
> tool is currently turned off (schema commented out in `tools.py`); the node,
> tool, and registry entry stay in place to re-enable it in one edit.

**Why this shape:**

- **The supervisor stays light.** A specialist runs its own multi-step tool loop
  in its *own* context — the coder's file reads, the researcher's raw web pages —
  and only its short final report crosses back. The supervisor's context never
  fills with that chatter, so the small local model stays fast.
- **Right model for the job.** The supervisor runs on a small **local** model
  (good enough for chat + tool calls). Code and web research go to capable
  **cloud** models that the local box couldn't run.
- **Real hand-offs.** Delegation is a tool call the graph intercepts and routes
  with `Command(goto=...)`; the specialist node runs, then hands control back to
  the supervisor with its report as the tool result.

### The agents and their tools

| Agent | Model (default) | Tools |
|---|---|---|
| **Supervisor** | local `mounir` (Ollama) — or Mistral / Groq | `read_file`, `write_file`, `edit_file`, `list_directory`, `open_browser`, `open_path`, `bash`, `send_email`, `delegate_to_researcher`, `delegate_to_media` |
| **Researcher** | `nvidia/llama-3.3-nemotron-super-49b-v1.5` (NVIDIA) | `web_search`, `search_news`, `fetch_url` |
| **Coder** *(delegation off)* | `minimaxai/minimax-m3` (NVIDIA) | `read_file`, `create_file`, `modify_file`, `delete_file`, `search_file` |
| **Media** | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` (NVIDIA) | `load_media`, `sample_frames`, `find_media` |

The supervisor has **no web access of its own** — all lookups go through the
researcher; anything it needs to *see or hear* (an image, a PDF, a screenshot, an
audio clip, a video) goes to the **media** agent, which loads the bytes, lets an
omni model read them, and returns a text report. Each specialist's tools are
isolated to its own module, so its work never leaks into the supervisor's context.

### Notable tool details (inspired by agentic file editors)

- **`read_file`** returns content with line numbers (`cat -n` style) and pages in
  chunks (default 300 lines for the supervisor, 1200 for the coder) — continue
  with `start_line` instead of dumping a whole file into a small model.
- **`edit_file` / `modify_file`** do surgical exact-string replacement (with an
  optional `replace_all`) instead of rewriting files — and **refuse to edit a
  file that wasn't read first**, so the model never blind-edits text it hasn't seen.
- **`bash`** runs shell commands behind a confirmation prompt, with a per-call
  `timeout` and a `run_in_background` flag for long-running processes.
- **`open_path`** opens any file/folder/URL with the system default app (xdg-open).
- **`send_email`** sends via SMTP with optional file attachments (also confirmed).
  You can email a saved contact **by name**: the model reads the address book
  (`knowledge/contacts.md`) and sends to the listed address — handy for voice,
  where spelling out an email is painful. After a send to an unknown address it's
  prompted to save the new contact; if a name isn't on file it asks rather than
  guessing.
- **`load_media` / `sample_frames`** (media agent) attach a file's bytes to the
  agent's own conversation so the omni model can analyse it directly — images and
  audio inline, PDFs as extracted text (or page images when scanned), and video as
  sampled keyframes. Optional deps are loaded only when used (`Pillow`, `pypdf`,
  `PyMuPDF`, `opencv-python`).

### Memory

Conversation memory persists the **full turn** — every assistant tool call and
its result — so on a follow-up the supervisor still remembers the path it found
or the source the researcher returned, instead of redoing the work. A rolling
window keeps it bounded, and the window is kept valid (every tool result stays
paired with the call that produced it).

---

## Quick start

### Prerequisites

- **Python 3.10+**
- **[Ollama](https://ollama.com)** running locally (for the supervisor model)
- An **[NVIDIA build.nvidia.com](https://build.nvidia.com) API key** — powers the
  coder and researcher specialists (free tier available)

### Install

```bash
# 1. Build the custom 'mounir' build (personality + params). The base model is
#    set inside the Modelfile; `ollama create` pulls it automatically.
ollama create mounir -f modelfiles/mounir.Modelfile

# 2. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Specialist API key (coder + researcher). Persist it in ~/.bashrc:
#    export NVIDIA_API_KEY="nvapi-..."
export NVIDIA_API_KEY="nvapi-..."

# 4. Talk to Mounir
python cli.py
```

Prefer to skip the custom build? Point at the base model — the personality is
injected from `config.py`:

```bash
MOUNIR_MODEL=qwen3:4b python cli.py
```

### REPL commands

`/reset` · `/save` · `/load` · `/think` · `/exit`

---

## Choosing the supervisor's model

By default the supervisor runs **locally** through Ollama (`MOUNIR_MODEL`, default
`mounir`). To run it on a hosted model instead, flip a provider flag and supply
its key:

```bash
# Mistral
USE_MISTRAL=true  MISTRAL_API_KEY=...  python cli.py     # mistral-small-latest

# Groq
USE_GROQ=true     GROQ_API_KEY=...     python cli.py     # qwen/qwen3-32b
```

The **coder and researcher always use NVIDIA** (`NVIDIA_API_KEY`), independent of
the supervisor's provider.

---

## Voice mode

```bash
sudo apt install portaudio19-dev            # system audio lib
pip install -r requirements-voice.txt       # whisper, piper, sounddevice, …

# Download a Piper voice into ~/.mounir/voices/
mkdir -p ~/.mounir/voices && cd ~/.mounir/voices
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json

python voice_cli.py            # push-to-talk: Enter to start/stop
python voice_cli.py --wake     # hands-free: say the wake word, then talk
```

Hands-free triggers on a wake word (`MOUNIR_WAKE_WORD`, default `hey_jarvis`;
openwakeword built-ins: `hey_jarvis`, `alexa`, `hey_mycroft`), then auto-detects
when you stop speaking (energy-based silence detection). A custom **"Hey Mounir"**
needs a short openwakeword training run — not done yet; `hey_jarvis` works today.

For Arabic TTS, grab an `ar_*` Piper voice and set `MOUNIR_PIPER_MODEL` to it.

> openwakeword's `tflite-runtime` dep has no wheel on Python 3.13. Install it
> without deps (`pip install openwakeword --no-deps`); the ONNX runtime deps are
> already in `requirements-voice.txt`.

---

## Web dashboard

A FastAPI app serves a chat + live CPU/RAM/network dashboard.

```bash
sudo apt install ffmpeg
pip install fastapi uvicorn psutil python-multipart
python server.py            # http://localhost:8000
```

---

## Configuration (environment variables)

Everything tunable lives in `mounir/config.py` and is overridable by env var.

**Core / supervisor model**

| Variable | Default | Purpose |
|---|---|---|
| `MOUNIR_MODEL` | `mounir` | Ollama model for the supervisor |
| `MOUNIR_THINK` | `false` | Qwen3 thinking mode (slower, smarter) |
| `MOUNIR_MAX_HISTORY` | `20` | Recent messages kept in the prompt window |
| `MOUNIR_DATA_DIR` | `~/.mounir` | Where conversations/voices are stored |
| `MOUNIR_LOCATION` | `Tunis, Tunisia` | Location given to the model as context |

**Specialists (NVIDIA — required for coding / research)**

| Variable | Default | Purpose |
|---|---|---|
| `NVIDIA_API_KEY` | – | Key for the coder + researcher (required) |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible endpoint |
| `CODER_MODEL` | `minimaxai/minimax-m3` | Coder model |
| `RESEARCHER_MODEL` | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | Researcher model |
| `MEDIA_MODEL` | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | Media (omni) model |

**Alternative supervisor providers (optional)**

| Variable | Default | Purpose |
|---|---|---|
| `USE_MISTRAL` / `MISTRAL_API_KEY` / `MISTRAL_MODEL` | `false` / – / `mistral-small-latest` | Run the supervisor on Mistral |
| `USE_GROQ` / `GROQ_API_KEY` / `GROQ_MODEL` | `false` / – / `qwen/qwen3-32b` | Run the supervisor on Groq |

**Email (`send_email`)**

| Variable | Default | Purpose |
|---|---|---|
| `MOUNIR_SMTP_HOST` / `MOUNIR_SMTP_PORT` | `smtp.gmail.com` / `587` | SMTP server |
| `MOUNIR_SMTP_USER` / `MOUNIR_SMTP_PASS` | – | Email + **Gmail App Password** (not your login password) |
| `MOUNIR_IMAP_HOST` | `imap.gmail.com` | IMAP server for reading the inbox |
| `MOUNIR_KNOWLEDGE_DIR` | `knowledge/` | Folder for the `contacts.md` address book |

**Voice / wake word**

| Variable | Default | Purpose |
|---|---|---|
| `MOUNIR_WHISPER_MODEL` | `small` | Whisper size (`base` for more speed) |
| `MOUNIR_WHISPER_LANG` | auto | Force STT language (`en`, `ar`) |
| `MOUNIR_PIPER_MODEL` | `~/.mounir/voices/en_US-amy-medium.onnx` | TTS voice file |
| `MOUNIR_WAKE_WORD` | `hey_jarvis` | openwakeword trigger |
| `MOUNIR_WAKE_THRESHOLD` | `0.5` | Wake sensitivity |

---

## Target hardware

Developed against a CPU-only box — **Intel i5-8400 · 16 GB RAM · no GPU ·
Ubuntu**. The local supervisor model is kept small (a 4B-class build) for speed;
the heavy lifting is offloaded to the cloud specialists, which is the whole point
of the split.

---

## Project layout

```
cli.py                  text REPL (main entry point)
voice_cli.py            voice entry point (push-to-talk / --wake)
server.py               FastAPI web dashboard
index.html              the dashboard UI
modelfiles/             the 'mounir' Ollama Modelfile (personality + params)
requirements.txt        core deps
requirements-voice.txt  voice deps (on top of the core)
knowledge/
  contacts.md           address book (name → email) for send_email by name

mounir/
  langgraph_agent.py    the supervisor + coder + researcher + media graph
  agent.py              thin compatibility wrapper around the graph
  config.py             all tunables (env-overridable) + the supervisor prompt
  llm.py                provider clients (Ollama stream, Mistral, Groq, NVIDIA)
  tools.py              supervisor tools (files, bash, browser, email, delegation)
  memory.py             conversation history + full-turn persistence + JSON save
  trace.py              the purple, Claude-Code-style terminal renderer
  specialists/
    coder.py            coder agent + its isolated file tools
    researcher.py       researcher agent + its isolated web tools
    media.py            media agent + its isolated load/sample/find tools
  audio.py stt.py tts.py voice.py wakeword.py sentences.py   voice pipeline
```

---

## Status

Working: local text chat with the supervisor, the researcher and media
specialists (the coder is wired but its delegation is currently off), email
including send-by-name from the contacts file, full-turn memory, voice
(push-to-talk + hands-free), and the web dashboard. Ongoing: a custom "Hey
Mounir" wake word, more tools, and long-term memory of facts about the user.
