"""
FastAPI backend for the Mounir web UI.

Serves:
  - GET  /                 -> the dashboard (index.html)
  - GET  /api/stats        -> live CPU/RAM/network stats (psutil)
  - WS   /ws/chat          -> text chat, streams Mounir's reply token by token
  - POST /api/voice        -> upload audio, returns transcript + spoken reply (base64 wav)

Run with:
    pip install fastapi uvicorn psutil python-multipart
    python server.py

Then open http://localhost:8000
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import threading
import time
import uuid
from pathlib import Path

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mounir.agent import Agent
from mounir import config as cfg, db, llm
from mounir import mcp_agents
from mounir import stt, tts, audio as audio_mod, tools
from mounir.specialists.mcp_agent import discover_tools

ROOT_DIR = Path(__file__).resolve().parent
WEB_PORT = int(os.environ.get("MOUNIR_WEB_PORT", "8000"))
_default_hosts = "localhost,127.0.0.1,::1"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("MOUNIR_WEB_ALLOWED_HOSTS", _default_hosts).split(",")
    if host.strip()
]
_default_origins = (
    f"http://localhost:{WEB_PORT},http://127.0.0.1:{WEB_PORT},"
    f"http://[::1]:{WEB_PORT}"
)
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.environ.get(
        "MOUNIR_WEB_ALLOWED_ORIGINS", _default_origins
    ).split(",")
    if origin.strip()
}

app = FastAPI(title="Mounir")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

# Ensure the SQLite DB exists and the legacy JSON file is migrated.
db.init()

# One shared agent instance = one shared conversation memory across the UI.
agent = Agent()
_agent_lock = threading.Lock()

# --- in-browser tool confirmation -------------------------------------------
# Tools like bash and the email agent's send gate on tools.confirm_fn. By
# default that prompts on the SERVER's terminal, which is useless from the web
# UI. We redirect it here: send the action to the open browser over the chat
# WebSocket and block the worker thread until the user clicks Confirm/Cancel.
_ui = {"ws": None, "loop": None, "out": None}     # the live dashboard socket
_pending: dict[str, tuple[threading.Event, dict]] = {}  # confirm id -> (event, result)


def _web_confirm(action: str) -> bool:
    """Confirm hook called from the agent's worker thread.

    Pushes a confirm request to the browser and waits for the answer. If no
    dashboard is connected, refuse the outward action rather than hang.
    """
    ws, loop, out = _ui["ws"], _ui["loop"], _ui["out"]
    if ws is None or loop is None or out is None:
        return False

    cid = uuid.uuid4().hex
    event = threading.Event()
    result = {"approved": False}
    _pending[cid] = (event, result)
    loop.call_soon_threadsafe(
        out.put_nowait, {"type": "confirm", "id": cid, "prompt": action}
    )
    answered = event.wait(timeout=300)  # 5 min, then treat as cancel
    _pending.pop(cid, None)
    return result["approved"] if answered else False


tools.confirm_fn = _web_confirm

# --- network rate tracking ---------------------------------------------------
_last_net = psutil.net_io_counters()
_last_net_time = time.time()


def _read_html(filename: str) -> str:
    with (ROOT_DIR / filename).open("r", encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
async def root():
    return _read_html("index.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin():
    return _read_html("admin.html")


@app.get("/api/stats")
async def stats():
    global _last_net, _last_net_time

    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory().percent

    now = psutil.net_io_counters()
    t = time.time()
    dt = max(t - _last_net_time, 0.01)
    down_kbs = (now.bytes_recv - _last_net.bytes_recv) / 1024 / dt
    up_kbs = (now.bytes_sent - _last_net.bytes_sent) / 1024 / dt
    _last_net, _last_net_time = now, t

    return JSONResponse({
        "cpu": cpu,
        "ram": ram,
        "net_down": max(down_kbs, 0),
        "net_up": max(up_kbs, 0),
    })


@app.get("/api/conversation")
async def conversation_history():
    """Restore the visible chat when the dashboard page is opened again."""
    return {"messages": agent.conversation.display_messages()}


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    """Text chat + tool confirmation over one WebSocket.

    Client -> server:
      {"type": "user", "text": "..."}                   a chat message
      {"type": "confirm_response", "id": "..", "approved": bool}
    Server -> client:
      {"type": "chunk", "text": "..."}                  partial reply token
      {"type": "confirm", "id": "..", "prompt": "..."}  tool wants approval
      {"type": "done"}                                  reply finished
      {"type": "error", "message": "..."}

    The reply is produced on a worker thread; a sender task drains an outgoing
    queue so the receive loop stays free to handle confirm responses while the
    agent is still working (otherwise a tool waiting on confirmation deadlocks).
    """
    origin = ws.headers.get("origin")
    if origin not in ALLOWED_ORIGINS:
        await ws.close(code=1008, reason="Origin not allowed")
        return
    await ws.accept()
    loop = asyncio.get_event_loop()
    out: asyncio.Queue = asyncio.Queue()
    _ui.update(ws=ws, loop=loop, out=out)
    busy = {"flag": False}

    async def sender():
        while True:
            msg = await out.get()
            if msg is None:
                break
            try:
                await ws.send_json(msg)
            except Exception:
                break

    sender_task = asyncio.create_task(sender())

    def produce(text: str):
        try:
            with _agent_lock:
                for chunk in agent.respond(text):
                    loop.call_soon_threadsafe(
                        out.put_nowait, {"type": "chunk", "text": chunk}
                    )
        except Exception as exc:
            loop.call_soon_threadsafe(out.put_nowait, {"type": "error", "message": str(exc)})
        finally:
            loop.call_soon_threadsafe(out.put_nowait, {"type": "done"})
            busy["flag"] = False

    try:
        while True:
            data = await ws.receive_json()
            kind = data.get("type")
            if kind == "confirm_response":
                entry = _pending.get(data.get("id"))
                if entry:
                    entry[1]["approved"] = bool(data.get("approved"))
                    entry[0].set()
            elif kind == "user":
                if busy["flag"]:
                    continue  # one turn at a time (shared conversation)
                busy["flag"] = True
                loop.run_in_executor(None, produce, data.get("text", ""))
    except WebSocketDisconnect:
        pass
    finally:
        # Release any worker thread still blocked on a confirm, then tear down.
        for event, result in _pending.values():
            result["approved"] = False
            event.set()
        await out.put(None)
        sender_task.cancel()
        if _ui.get("ws") is ws:
            _ui.update(ws=None, loop=None, out=None)


@app.post("/api/voice")
async def voice_turn(file: UploadFile = File(...)):
    """Accept a recorded WAV clip, transcribe it, run the agent, return:
    { "text": user_text, "reply": full_reply_text, "audio_b64": <wav bytes> }

    The browser records audio (MediaRecorder) and posts it here; this mirrors
    voice.py's _handle_utterance but over HTTP for the web UI.
    """
    import numpy as np
    import soundfile as sf
    import subprocess

    raw = await file.read()

    # Browser MediaRecorder sends WebM/Opus — soundfile/libsndfile can't read
    # that directly, so convert to WAV via ffmpeg first (in-memory, no temp files).
    proc = subprocess.run(
        ["ffmpeg", "-i", "pipe:0", "-f", "wav", "-ar", "16000", "-ac", "1", "pipe:1"],
        input=raw,
        capture_output=True,
    )
    if proc.returncode != 0:
        return JSONResponse(
            {"error": f"ffmpeg conversion failed: {proc.stderr.decode(errors='ignore')[-300:]}"},
            status_code=500,
        )

    wav_np, sr = sf.read(io.BytesIO(proc.stdout), dtype="float32")
    if wav_np.ndim > 1:
        wav_np = wav_np.mean(axis=1)

    text, lang = stt.transcribe(wav_np)
    if not text:
        return JSONResponse({"text": "", "reply": "", "audio_b64": ""})

    # Run the (blocking) agent off the event loop so a tool confirmation can
    # still be delivered to the browser over the chat WebSocket mid-turn.
    loop = asyncio.get_event_loop()
    def respond() -> str:
        with _agent_lock:
            return "".join(agent.respond(text))

    reply = await loop.run_in_executor(None, respond)

    # Synthesize reply to WAV bytes (in-memory, no playback on server side).
    audio_b64 = ""
    try:
        voice = tts._load()
        pcm_parts = []
        sample_rate = 22050
        for chunk in voice.synthesize(reply):
            pcm_parts.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
            sample_rate = chunk.sample_rate
        if pcm_parts:
            pcm = np.concatenate(pcm_parts)
            buf = io.BytesIO()
            sf.write(buf, pcm, sample_rate, format="WAV", subtype="PCM_16")
            audio_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        pass  # UI will just show text if TTS isn't set up

    return JSONResponse({"text": text, "lang": lang, "reply": reply, "audio_b64": audio_b64})


# --- Admin: models, MCP servers, subagents ------------------------------------

@app.get("/api/agent-overview")
async def agent_overview():
    """Return the configured, user-visible agent topology for Agent Studio."""
    if cfg.USE_MISTRAL:
        supervisor_provider = "Mistral"
    elif cfg.USE_GROQ:
        supervisor_provider = "Groq"
    else:
        supervisor_provider = "Ollama (local)"

    return {
        "supervisor": {
            "name": "Mounir",
            "model": llm.active_model(agent.model),
            "provider": supervisor_provider,
        },
        "builtins": [
            {
                "name": "Researcher",
                "model": cfg.RESEARCHER_MODEL,
                "provider": "Ollama Cloud",
                "description": "Web research and current information",
            },
            {
                "name": "Media",
                "model": cfg.MEDIA_MODEL,
                "provider": "NVIDIA",
                "description": "Images, documents, audio and video",
            },
            {
                "name": "Knowledge",
                "model": cfg.KNOWLEDGE_MODEL,
                "provider": "Gemini",
                "description": "Long-term knowledge management",
            },
            {
                "name": "System",
                "model": cfg.SYSTEM_MODEL,
                "provider": "NVIDIA",
                "description": "Computer and hardware controls",
            },
            {
                "name": "Email",
                "model": cfg.EMAIL_MODEL,
                "provider": "Ollama Cloud",
                "description": "Email through Gmail MCP",
            },
        ],
    }

@app.get("/api/models")
async def list_models():
    return db.list_models()


@app.post("/api/models")
async def create_model(req: dict):
    try:
        return db.add_model(
            req.get("name", ""),
            req.get("model", ""),
            req.get("provider", ""),
            req.get("base_url", ""),
            req.get("api_key", ""),
        )
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.put("/api/models/{model_id}")
async def update_model(model_id: int, req: dict):
    try:
        m = db.update_model(model_id, **req)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not m:
        return JSONResponse({"error": "Model not found or in use."}, status_code=404)
    return m


@app.delete("/api/models/{model_id}")
async def delete_model(model_id: int):
    if db.delete_model(model_id):
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Model not found or in use."}, status_code=404)


@app.get("/api/mcp-servers")
async def list_servers():
    return db.list_servers()


@app.post("/api/mcp-servers")
async def create_server(req: dict):
    try:
        return db.add_server(
            req.get("name", ""),
            req.get("connection", ""),
            transport=req.get("transport", "stdio"),
            headers=req.get("headers", "{}"),
            env=req.get("env", "{}"),
        )
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.put("/api/mcp-servers/{server_id}")
async def update_server(server_id: int, req: dict):
    try:
        s = db.update_server(server_id, **req)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not s:
        return JSONResponse({"error": "Server not found or in use."}, status_code=404)
    return s


@app.post("/api/mcp-servers/{server_id}/test")
async def test_server(server_id: int):
    spec = db.build_server_spec(server_id)
    if spec is None:
        return JSONResponse({"error": "Server not found."}, status_code=404)
    try:
        tools_found = await asyncio.wait_for(discover_tools(spec), timeout=45)
    except TimeoutError:
        return JSONResponse(
            {"error": "Connection timed out after 45 seconds."}, status_code=504
        )
    except Exception as exc:
        # AnyIO transports may wrap connection failures in an ExceptionGroup.
        from mounir.specialists.mcp_agent import _exc_detail

        return JSONResponse({"error": _exc_detail(exc)}, status_code=400)
    return {"ok": True, "tools": tools_found}


@app.delete("/api/mcp-servers/{server_id}")
async def delete_server(server_id: int):
    if db.delete_server(server_id):
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Server not found or in use."}, status_code=404)


@app.get("/api/subagents")
async def list_subagents():
    return db.list_subagents()


@app.post("/api/subagents")
async def create_subagent(req: dict):
    try:
        mcp_agents._validate_agent_name(req.get("name", ""))
        return db.add_subagent(
            req.get("name", ""),
            req.get("description", ""),
            req.get("system_prompt", ""),
            int(req.get("model_id", 0)),
            int(req.get("mcp_server_id", 0)),
            confirm_tool_calls=req.get("confirm_tool_calls", True),
            parent="supervisor",
        )
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.put("/api/subagents/{subagent_id}")
async def update_subagent(subagent_id: int, req: dict):
    try:
        if "name" in req:
            mcp_agents._validate_agent_name(req["name"], exclude_id=subagent_id)
        s = db.update_subagent(subagent_id, **req)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not s:
        return JSONResponse({"error": "Subagent not found."}, status_code=404)
    return s


@app.delete("/api/subagents/{subagent_id}")
async def delete_subagent(subagent_id: int):
    if db.delete_subagent(subagent_id):
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Subagent not found."}, status_code=404)


if __name__ == "__main__":
    import uvicorn
    # This app can execute shell commands and stores credentials. Keep it on
    # loopback unless the owner explicitly opts into network exposure.
    uvicorn.run(
        app,
        host=os.environ.get("MOUNIR_WEB_HOST", "127.0.0.1"),
        port=WEB_PORT,
    )
