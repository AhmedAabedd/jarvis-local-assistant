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
import time

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from mounir.agent import Agent
from mounir import stt, tts, audio as audio_mod

app = FastAPI(title="Mounir")

# One shared agent instance = one shared conversation memory across the UI.
agent = Agent()

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
    """Text chat over WebSocket. Client sends raw text, server streams reply chunks.

    Message protocol (server -> client), one JSON object per line:
      {"type": "chunk", "text": "..."}      partial token
      {"type": "tool",  "name": "...", "args": {...}}   tool call fired
      {"type": "done"}                      reply finished
      {"type": "error", "message": "..."}
    """
    await ws.accept()
    try:
        while True:
            user_input = await ws.receive_text()
            try:
                # agent.respond is a sync generator; run it in a thread so we
                # don't block the event loop, forwarding chunks as they arrive.
                loop = asyncio.get_event_loop()
                queue: asyncio.Queue = asyncio.Queue()

                def produce():
                    try:
                        for chunk in agent.respond(user_input):
                            loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
                    except Exception as exc:
                        loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))
                    finally:
                        loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

                loop.run_in_executor(None, produce)

                while True:
                    kind, payload = await queue.get()
                    if kind == "chunk":
                        await ws.send_json({"type": "chunk", "text": payload})
                    elif kind == "error":
                        await ws.send_json({"type": "error", "message": payload})
                        break
                    elif kind == "done":
                        await ws.send_json({"type": "done"})
                        break
            except Exception as exc:
                await ws.send_json({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass


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

    reply_parts = []
    for chunk in agent.respond(text):
        reply_parts.append(chunk)
    reply = "".join(reply_parts)

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
