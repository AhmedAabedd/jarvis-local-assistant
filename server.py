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
import threading
import time
import uuid

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from mounir.agent import Agent
from mounir import stt, tts, audio as audio_mod, tools

app = FastAPI(title="Mounir")

# One shared agent instance = one shared conversation memory across the UI.
agent = Agent()

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


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


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
            for chunk in agent.respond(text):
                loop.call_soon_threadsafe(out.put_nowait, {"type": "chunk", "text": chunk})
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
    reply = await loop.run_in_executor(None, lambda: "".join(agent.respond(text)))

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
