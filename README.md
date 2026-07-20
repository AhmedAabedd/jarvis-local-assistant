# Mounir — a local, private AI assistant

Mounir is a Jarvis-style assistant that runs on **your own machine**. The brain
you chat with is a small **local LLM** (via [Ollama](https://ollama.com)); it
can read and edit files, run shell commands, open apps, send email, read media
(images, PDFs, audio, video), and — when a job needs more horsepower — hand off
to specialist agents in the cloud. No data leaves your box unless a tool
explicitly sends it.

It also speaks: optional voice mode transcribes your speech (Whisper), thinks,
and talks back (Piper). A FastAPI web dashboard and a separate admin UI handle
chat and runtime configuration.

> Personal project. Sharp, no-fluff personality by design.

This README is written to be useful to **any future contributor or AI agent**
working on the codebase. If you are about to change something, read the
architecture and project-layout sections first.

---

## Table of contents

1. [Architecture](#architecture)
2. [Built-in specialists](#built-in-specialists)
3. [Dynamic MCP subagents](#dynamic-mcp-subagents)
4. [Memory](#memory)
5. [Quick start](#quick-start)
6. [Configuration](#configuration)
7. [Voice](#voice)
8. [Web dashboard & admin](#web-dashboard--admin)
9. [Target hardware](#target-hardware)
10. [Project layout](#project-layout)
11. [Status & roadmap](#status--roadmap)
12. [Notes for AI contributors](#notes-for-ai-contributors)

---

## Architecture

Mounir is a **supervisor + specialists** graph built with
[LangGraph](https://github.com/langchain-ai/langgraph)). The supervisor is the
assistant you talk to; it does general work itself and **delegates** the heavy,
context-hungry jobs to isolated specialist agents, getting back only a compact
report.

```
                       ┌──────────────────┐
   you ── text ──────▶ │    SUPERVISOR    │ ── answer ──▶ you
                       │   (local LLM)    │
                       │  files · bash ·  │
                       │ browser ·youtube │
                       └────────┬─────────┘
            delegate_to_<name> tool call — Command(goto=…) hand-off
      └───────────┬───────────┬────┴──────┬───────────┬───────────┘
      ▼           ▼           ▼           ▼           ▼           ▼
┌──────────┐┌──────────┐┌──────────┐┌──────────┐┌──────────┐┌──────────┐
│RESEARCHER││  MEDIA   ││KNOWLEDGE ││  SYSTEM  ││  EMAIL   ││MCP AGENTS│
│ollama.com││ NVIDIA  ││ Gemini  ││ NVIDIA  ││ dynamic  ││(dynamic) │
│web search││img · pdf ││facts ·   ││vol·bri·wl││Gmail MCP ││any MCP   │
│fetch page││audio·vid ││contacts  ││media·pwr ││registry  ││server    │
└─────┴───────────┴─────────── report back ───────────┴───────────┴────┘
```

> The **coder** node (NVIDIA) is wired the same way, but its
> `delegate_to_coder` schema is commented out in `tools.py` so the supervisor
> cannot delegate to it right now. Re-enable it by uncommenting the schema.

### How the graph works

The graph compiles **once per user turn** in `mounir/langgraph_agent.py`.
This matters because it means:

- Newly registered dynamic MCP subagents are live from the **next message**,
  with no restart.
- The supervisor's tool list is rebuilt every turn from `tools.SCHEMAS` plus
  one `delegate_to_<slug>` schema per registered subagent.

Key pieces in `langgraph_agent.py`:

- `_DELEGATES` maps `delegate_to_*` tool names to graph node names.
- `_supervisor()` is the main node. It streams the local model's reply. If the
  model calls a delegate tool, the node returns `Command(goto=<node>)` instead
  of running the tool inline.
- Specialist nodes (`_coder`, `_researcher`, `_media`, `_knowledge`, `_system`,
  and the dynamic `_make_mcp_node` nodes) extract the
  task from the delegate call, run their own loop, and return
  `Command(goto="supervisor")` with a short report.
- `_count_delegations()` caps how many hand-offs can happen per turn (currently
  `MAX_DELEGATIONS = 3`).
- `Agent.respond()` is the public API used by `cli.py`, `voice_cli.py`,
  `telegram_cli.py`, and `server.py`; it yields reply chunks and persists the
  full turn to memory.

### Why this shape

- **The supervisor stays light.** A specialist runs its own multi-step tool loop
  in its *own* context — the coder's file reads, the researcher's raw web pages —
  and only its short final report crosses back. The supervisor's context never
  fills with that chatter, so the small local model stays fast.
- **Right model for the job.** The supervisor runs on a small **local** model
  (good enough for chat + tool calls). Code, web research, media, hardware,
  and knowledge go to capable **cloud** models the local box couldn't run.
- **Real hand-offs.** Delegation is a tool call the graph intercepts and routes
  with `Command(goto=...)`; the specialist node runs, then hands control back to
  the supervisor with its report as the tool result.

---

## Built-in specialists

| Agent | Model (default) | Provider | Tools |
|---|---|---|---|
| **Supervisor** | local `mounir` (Ollama) — or Mistral / Groq | local / hosted | `read_file`, `write_file`, `edit_file`, `list_directory`, `open_browser`, `play_on_youtube`, `open_path`, `bash` + one `delegate_to_*` tool per specialist |
| **Researcher** | `nemotron-3-super:cloud` | Ollama Cloud | `web_search`, `search_news`, `fetch_url` |
| **Media** | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | NVIDIA | `load_media`, `sample_frames`, `find_media` |
| **Knowledge** | `gemini-2.5-flash` | Google Gemini | `list_knowledge`, `read_knowledge`, `search_knowledge`, `save_knowledge`, `append_knowledge`, `delete_knowledge` |
| **System** | `meta/llama-3.1-8b-instruct` | NVIDIA | `set_volume`, `set_brightness`, `set_keyboard_backlight`, `media_control`, `system_status`, `set_wifi`, `set_bluetooth`, `lock_screen`, `suspend` |
| **Coder** *(delegation off)* | `minimaxai/minimax-m3` | NVIDIA | `read_file`, `create_file`, `modify_file`, `delete_file`, `search_file` |

Rules of thumb the supervisor prompt encodes:

- The supervisor has **no web access of its own** — all lookups go through the
  researcher.
- Anything it needs to *see or hear* (image, PDF, screenshot, audio, video)
  goes to the **media** agent.
- Long-term knowledge changes go to the **knowledge** agent.
- Hardware/system control goes to the **system** agent.
- Anything Gmail goes to the dynamic **Email** MCP agent seeded in the registry.
- Each specialist's tools are isolated to its own module; its raw work never
  leaks into the supervisor's context.

### Notable tool details

- **`read_file`** returns content with line numbers (`cat -n` style) and pages in
  chunks (default 300 lines for the supervisor, 1200 for the coder) — continue
  with `start_line` instead of dumping a whole file into a small model.
- **`edit_file` / `modify_file`** do surgical exact-string replacement (with an
  optional `replace_all`) instead of rewriting files — and **refuse to edit a
  file that wasn't read first**, so the model never blind-edits text it hasn't seen.
- **`bash`** runs shell commands behind a confirmation prompt, with a per-call
  `timeout` and a `run_in_background` flag for long-running processes.
- **`open_path`** opens any file/folder/URL with the system default app (xdg-open).
- **`play_on_youtube`** resolves a search to the top YouTube result via yt-dlp
  and opens it in the browser.
- **the dynamic Email agent** runs Gmail over an MCP server (OAuth — no IMAP/SMTP):
  it spawns the server per task, adopts whatever tools the server advertises,
  and confirms before sending or deleting. You can email a saved contact **by
  name**: the supervisor reads the address book (`knowledge/contacts.md`) and
  puts the real address in the delegated task. New contacts are stored via the
  knowledge agent.
- **`load_media` / `sample_frames`** (media agent) attach a file's bytes to the
  agent's own conversation so the omni model can analyse it directly — images and
  audio inline, PDFs as extracted text (or page images when scanned), and video as
  sampled keyframes. Optional deps are loaded only when used (`Pillow`, `pypdf`,
  `PyMuPDF`, `opencv-python`).

---

## Dynamic MCP subagents

You can register **specialists backed by any MCP server** — no code changes.
Email is now one of these dynamic agents and is seeded automatically during the
one-time upgrade from the former built-in implementation. The setup is
three-tier and persisted in a
SQLite DB at `~/.mounir/mounir.db`:

1. **Models** — reusable LLM presets: name, provider, base URL, and an optional
   API key entered directly in the admin form. The endpoint must implement OpenAI-compatible
   `/chat/completions` including function/tool calls; the provider field is a
   display label, not a separate provider adapter.
2. **MCP servers** — reusable connections. Use `stdio` for a local server
   process, Streamable HTTP for a remote server, or the deprecated HTTP+SSE
   transport only when an older server requires it. The admin form provides
   ordinary fields for bearer tokens, API keys, and local-server credentials;
   users do not edit JSON or export environment variables.
3. **Subagents** — the actual delegation targets: name, description (the
   routing signal for the small supervisor model), system prompt, plus a chosen
   model and MCP server.

Each subagent becomes one `delegate_to_<slug>` tool and one graph node. Only
its short report crosses back; the server's own tools never enter the
supervisor's context. Subagents are loaded when each turn compiles, so a new
one is live from your next message.

### How it is wired

- `mounir/db.py` owns the SQLite schema and CRUD.
- `mounir/mcp_agents.py` is the registry layer: slug helpers, schema building,
  and a management CLI. The server/CLI/runtime initialize the schema when used
  and migrate any legacy `~/.mounir/mcp_agents.json` without import-time writes.
- `mounir/specialists/mcp_agent.py` is the generic MCP client. It accepts a
  resolved spec, connects over stdio, Streamable HTTP, or legacy SSE, adopts
  all paginated tools the server advertises, loops with `llm.openai_chat`, and
  returns a short report.
- `mounir/langgraph_agent.py` adds one node per registered subagent at graph
  compile time and extends the delegate map so the supervisor can route to it.

### Management

Manage everything at **`http://localhost:8000/admin`**:

1. Create a model preset and paste its API key, if it needs one.
2. Create an MCP server and choose **Local server**, **Remote HTTP**, or
   **Legacy SSE**.
3. Paste the command or endpoint provided by the MCP server.
4. Choose the authentication method. Paste a bearer token/API key, or add the
   named credentials listed by a local server's instructions.
5. Save it and click **Test Connection** to see its advertised tools.
6. Create a subagent that links the model and server.

For the seeded **Gmail MCP** server, its detail screen also provides OAuth-file
upload and a **Connect Gmail** button. Authorization opens in the browser and
stores Google's credential files locally under `~/.gmail-mcp/`.
If Google's [OAuth publishing status](https://support.google.com/cloud/answer/15549945)
is **Testing**, Gmail refresh tokens expire after seven days. Agent Studio detects that state and changes the action to
**Reconnect Gmail**. Changing the OAuth app to **In production** removes the
Testing-mode seven-day limit; Google may require verification depending on the
account and requested scopes.
The preset keeps the existing
[`@gongrzhe/server-gmail-autoauth-mcp`](https://github.com/GongRzhe/Gmail-MCP-Server)
package for compatibility. Its upstream repository is archived, so review or
replace that MCP server before relying on it for a sensitive long-term setup.

Credentials entered through the UI are stored in the local SQLite database,
which Mounir restricts to the current operating-system user (`0600`). They are
not sent anywhere except the configured model or MCP endpoint.

The management CLI remains available for developers and automation:

```bash
python -m mounir.mcp_agents models list
python -m mounir.mcp_agents servers list
python -m mounir.mcp_agents agents list

python -m mounir.mcp_agents models add --name "Ollama Cloud" --model qwen3:4b \
  --provider Ollama --base-url https://ollama.com/v1 --api-key '$OLLAMA_API_KEY'

python -m mounir.mcp_agents servers add --name "Brave Search" \
  --transport stdio \
  --connection "npx -y @modelcontextprotocol/server-brave-search" \
  --env '{"BRAVE_API_KEY":"$BRAVE_API_KEY"}'

python -m mounir.mcp_agents servers add --name "Remote tools" \
  --transport streamable_http --connection "https://example.com/mcp" \
  --headers '{"Authorization":"Bearer $REMOTE_MCP_TOKEN"}'

python -m mounir.mcp_agents agents add --name "Web Search" \
  --description "Search the web with Brave. Use for any lookup." \
  --prompt "You are a web search specialist..." \
  --model-id 1 --server-id 1
```

The first time the new code runs, any existing `~/.mounir/mcp_agents.json` is
migrated into the DB. Today every dynamic agent reports to the supervisor;
nested parents (a subagent reporting to another subagent) are future work.
New subagents ask for confirmation before every MCP tool call by default.
The form can instead require approval only for named tools or allow all calls.
The seeded Email agent confirms only send/delete operations.
After saving a server, open it in the admin UI and use **Test Connection** to
verify the handshake and see the tools it advertises.

Mounir is the MCP **host** and contains a generic MCP **client** used by every
dynamic MCP agent, including Email. It does not expose an MCP server of its own.
The dynamic layer currently consumes MCP **tools**; it does not yet surface a
server's prompts/resources or a generic interactive MCP OAuth browser flow.
The seeded local Gmail integration has its own browser OAuth onboarding. Remote
servers currently work with no authentication, a pasted bearer
token, an API-key header, or advanced custom headers.

---

## Memory

Conversation memory persists the **full turn** — every assistant tool call and
its result — so on a follow-up the supervisor still remembers the path it found
or the source the researcher returned, instead of redoing the work. A rolling
window keeps it bounded, and the window is kept valid (every tool result stays
paired with the call that produced it).

Memory lives in `mounir/memory.py`. The `Agent` object keeps a `Conversation`
instance; `respond()` appends the turn produced by the graph to it.

---

## Quick start

### Prerequisites

- **Python 3.10+**
- **[Ollama](https://ollama.com)** running locally (for the supervisor model)
- Specialist keys (free tiers available; each powers different agents):
  - **[Ollama Cloud](https://ollama.com/settings/keys)** — researcher and the
    seeded dynamic Email agent (`OLLAMA_API_KEY`)
  - **[NVIDIA build.nvidia.com](https://build.nvidia.com)** — media, system,
    coder (`NVIDIA_API_KEY`)
  - **Google Gemini** — knowledge agent (`GEMINI_API_KEY`)

### Install

```bash
# 1. Build the custom 'mounir' build (personality + params). The base model is
#    set inside the Modelfile; `ollama create` pulls it automatically.
ollama create mounir -f modelfiles/mounir.Modelfile

# 2. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Specialist API keys. Persist them in ~/.bashrc:
export OLLAMA_API_KEY="..."        # researcher + seeded dynamic Email agent
export NVIDIA_API_KEY="nvapi-..."  # media + system + coder
export GEMINI_API_KEY="..."        # knowledge

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

## Configuration

Runtime defaults live in `mounir/config.py` and are overridable by environment variables.
Dynamic MCP subagents—including Email—are stored in SQLite and managed from Agent Studio.
The Email variables below are legacy seed overrides used only by the one-time migration
from the old built-in Email agent.

### Core / supervisor model

| Variable | Default | Purpose |
|---|---|---|
| `MOUNIR_MODEL` | `mounir` | Ollama model for the supervisor |
| `MOUNIR_THINK` | `false` | Qwen3 thinking mode (slower, smarter) |
| `MOUNIR_MAX_HISTORY` | `20` | Recent messages kept in the prompt window |
| `MOUNIR_DATA_DIR` | `~/.mounir` | Where conversations, voices, and the SQLite DB are stored |
| `MOUNIR_LOCATION` | `Tunis, Tunisia` | Location given to the model as context |

### Built-in specialists and Email migration defaults

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_API_KEY` | – | Ollama Cloud key — researcher and the seeded dynamic Email agent |
| `OLLAMA_CLOUD_BASE_URL` | `https://ollama.com/v1` | OpenAI-compatible endpoint |
| `RESEARCHER_MODEL` | `nemotron-3-super:cloud` | Researcher model |
| `EMAIL_MODEL` | `gpt-oss:120b-cloud` | One-time model override when seeding the dynamic Email agent |
| `GMAIL_MCP_COMMAND` | `npx -y @gongrzhe/server-gmail-autoauth-mcp` | One-time server-command override when seeding Email |
| `NVIDIA_API_KEY` | – | NVIDIA key — media, system, coder |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible endpoint |
| `MEDIA_MODEL` | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | Media (omni) model |
| `SYSTEM_MODEL` | `meta/llama-3.1-8b-instruct` | System (hardware control) model |
| `CODER_MODEL` | `minimaxai/minimax-m3` | Coder model |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | – / `gemini-2.5-flash` | Knowledge agent (Google's OpenAI-compatible endpoint) |
| `MOUNIR_KNOWLEDGE_DIR` | `knowledge/` | Folder the knowledge agent curates (`contacts.md` address book, facts, …) |

### Alternative supervisor providers (optional)

| Variable | Default | Purpose |
|---|---|---|
| `USE_MISTRAL` / `MISTRAL_API_KEY` / `MISTRAL_MODEL` | `false` / – / `mistral-small-latest` | Run the supervisor on Mistral |
| `USE_GROQ` / `GROQ_API_KEY` / `GROQ_MODEL` | `false` / – / `qwen/qwen3-32b` | Run the supervisor on Groq |

### Telegram bridge (`telegram_cli.py`)

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | – | Bot token from @BotFather (long-polling — nothing exposed) |
| `TELEGRAM_CHAT_ID` | – | The one chat allowed to talk to the assistant |

### Voice / wake word

| Variable | Default | Purpose |
|---|---|---|
| `MOUNIR_WHISPER_MODEL` | `small` | Whisper size (`base` for more speed) |
| `MOUNIR_WHISPER_LANG` | auto | Force STT language (`en`, `ar`) |
| `MOUNIR_PIPER_MODEL` | `~/.mounir/voices/en_US-amy-medium.onnx` | TTS voice file |
| `MOUNIR_WAKE_WORD` | `hey_jarvis` | openwakeword trigger |
| `MOUNIR_WAKE_THRESHOLD` | `0.5` | Wake sensitivity |

---

## Voice

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

## Web dashboard & admin

Two separate pages, both served by `server.py`:

- **`/`** — the chat dashboard: voice orb, transcript, live CPU/RAM/network
  stats, tool-confirmation modal. It talks to the same shared `Agent`
  instance over a WebSocket (`/ws/chat`).
- **`/admin`** — the management UI for models, MCP servers, and dynamic
  subagents. It uses the REST endpoints under `/api/models`, `/api/mcp-servers`,
  and `/api/subagents`.

```bash
sudo apt install ffmpeg
python server.py            # http://localhost:8000  +  http://localhost:8000/admin
```

The web app binds to `127.0.0.1` by default because it can execute local tools
and its admin API manages credentials. Set `MOUNIR_WEB_HOST` only if you
deliberately want network access, and put authentication in front of it before
exposing it beyond your machine. `MOUNIR_WEB_PORT` changes the default port.
If you deliberately use another hostname/origin, also set the comma-separated
`MOUNIR_WEB_ALLOWED_HOSTS` and `MOUNIR_WEB_ALLOWED_ORIGINS`; browser WebSockets
from other origins are rejected by default.

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
telegram_cli.py         Telegram bridge (long-polling bot)
server.py               FastAPI app: dashboard, admin, WebSocket, voice upload
index.html              the chat dashboard UI
admin.html              separate management UI for models / MCP servers / subagents
modelfiles/             the 'mounir' Ollama Modelfile (personality + params)
requirements.txt        core deps
requirements-voice.txt  voice deps (on top of the core)
knowledge/              long-term knowledge folder
  contacts.md           address book (name → email) for email-by-name
  email_style.md        style hints for the email agent
  index.md              auto-generated menu of knowledge files
  music-list.md         example personal list
  profile_report.md     example personal notes

mounir/
  __init__.py
  agent.py              thin compatibility wrapper around the LangGraph agent
  langgraph_agent.py    the supervisor + specialists graph (built-ins + dynamic MCP agents)
  config.py             all tunables (env-overridable) + the supervisor prompt
  llm.py                provider clients (Ollama stream, Mistral, Groq, NVIDIA, generic OpenAI)
  tools.py              supervisor tools (files, bash, browser, delegation)
  default_agents.py     one-time presets for migrated dynamic agents such as Email
  db.py                 SQLite persistence: models, MCP servers, subagents
  mcp_agents.py         registry layer + management CLI (uses db.py)
  memory.py             conversation history + full-turn persistence + JSON save
  trace.py              the purple, Claude-Code-style terminal renderer
  specialists/
    __init__.py
    coder.py            coder agent + its isolated file tools
    knowledge.py        knowledge agent: curates the knowledge folder
    media.py            media agent: reads images, PDFs, audio, video
    mcp_agent.py        generic MCP specialist that runs each registered subagent
    researcher.py       researcher agent: web search, news, page fetch
    system.py           system agent: volume, brightness, media, wifi, power
  audio.py              audio capture helpers
  stt.py                speech-to-text (local faster-whisper or Groq cloud)
  tts.py                text-to-speech (Piper local or Google Cloud)
  voice.py              voice pipeline orchestration
  wakeword.py           openwakeword integration
  sentences.py          sentence splitting for TTS
```

---

## Status & roadmap

**Working:** local text chat with the supervisor, all built-in specialists
(researcher, media, knowledge, system; coder is wired but delegation is off),
dynamic MCP subagents including Email registered at runtime (local stdio, remote
Streamable HTTP, and legacy SSE), full-turn memory, voice
(push-to-talk + hands-free), Telegram bridge, web dashboard, and admin UI.

**Ongoing / future:**
- Custom "Hey Mounir" wake word
- More built-in tools
- Long-term memory of facts about the user
- Persistent MCP connection pool (servers currently spawn per task)
- Nested subagent hierarchies (a subagent reporting to another subagent)

---

## Notes for AI contributors

### Conventions

- Keep the supervisor's tool list small and curated. It runs on a small local
  model; too many tools degrades routing quality.
- Specialists own their own tools and prompts. Never let a specialist's raw
  tool chatter leak into the supervisor's context — only a compact report.
- File edits are exact-string replacement (`edit_file` / `modify_file`), not
  full rewrites, and require the file to have been read first.
- `tools.confirm_fn` gates outward-facing actions. The CLI uses a terminal
  prompt; `server.py` swaps in a WebSocket confirmation so the browser can
  approve.
- API keys for built-in specialists come from environment variables. Dynamic
  model and MCP credentials are entered through the admin UI and stored in the
  user-only SQLite database.

### Adding a built-in specialist

1. Create `mounir/specialists/<name>.py` with a `run(task: str) -> str` function.
2. Add a node function in `mounir/langgraph_agent.py` following the existing
   pattern (extract delegate task, call `run`, trace, return `Command`).
3. Add the delegate tool name → node name mapping to `_DELEGATES`.
4. Add the schema to `tools.py` so the supervisor is offered the tool.
5. Mention it in `config.SYSTEM_PROMPT` so the model knows when to use it.
6. Update this README's agents table.

### Adding a dynamic MCP subagent

No code changes. Use the admin UI at `/admin` or the CLI:

1. Add a **model** preset. The **Model ID** is the actual identifier the API
   expects (e.g. `qwen3:4b`, `gpt-4o`); **Name** is just the display label.
2. Add an **MCP server** connection (stdio command or Streamable HTTP URL;
   choose SSE only for a legacy server), save it, and test the connection.
3. Add a **subagent** linking the two. The description field is the routing
   signal — write it so the small supervisor model knows when to delegate.

### Testing changes

- `python -m compileall -q mounir server.py` for syntax.
- `python -m mounir.mcp_agents agents list` to inspect the dynamic registry.
- `python server.py` and open `http://localhost:8000/admin` for UI checks.
- Run a quick graph build: `python -c "from mounir.langgraph_agent import build_graph; build_graph()"`.
