"""
FastAPI backend for the Mounir web UI.

Serves:
  - GET  / and /admin      -> the compiled React application
  - WS   /ws/chat          -> text chat, streams Mounir's reply token by token
  - POST /api/voice        -> upload audio, returns transcript + spoken reply (base64 wav)

Also owns the heartbeat scheduler, Telegram long-polling bridge, and signed
WhatsApp Cloud API webhook. Every channel keeps separate conversation history,
while agent turns remain serialized for safe access to shared tools.

Run with:
    pip install fastapi uvicorn psutil python-multipart
    python server.py

Then open http://localhost:8000
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import shlex
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mounir.agent import Agent
from mounir import config as cfg, db, llm, mcp_oauth, trace
from mounir import mcp_agents
from mounir import stt, tts, audio as audio_mod, tools
from mounir.heartbeat import HeartbeatService
from mounir.specialists.mcp_agent import discover_tools
from mounir.telegram_bridge import TelegramBridge
from mounir.whatsapp_bridge import WhatsAppBridge

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
    f"http://[::1]:{WEB_PORT},http://localhost:5173,"
    "http://127.0.0.1:5173,http://[::1]:5173"
)
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.environ.get(
        "MOUNIR_WEB_ALLOWED_ORIGINS", _default_origins
    ).split(",")
    if origin.strip()
}
SUBAGENT_ICON_MAX_BYTES = 512 * 1024


class _OAuthSetupRun:
    def __init__(self):
        self.authorization_url = ""
        self.error = ""
        self.status = "starting"
        self.redirect_ready = asyncio.Event()
        self.callback = asyncio.Queue(maxsize=1)
        self.task: asyncio.Task | None = None


_oauth_setup_runs: dict[int, _OAuthSetupRun] = {}

# Ensure the SQLite DB exists and the legacy JSON file is migrated.
db.init()

# Every channel uses a separate context window so messages never leak between
# interfaces. ``agent`` remains the web alias for compatibility with existing
# integrations and tests.
agent = Agent()
telegram_agent = Agent()
whatsapp_agent = Agent()
_agent_lock = threading.Lock()

# --- in-browser tool confirmation -------------------------------------------
# Tools like bash and selected dynamic MCP actions gate on tools.confirm_fn. By
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


async def _deliver_heartbeat_alert(message: str, task: dict | None = None) -> None:
    """Deliver one proactive alert to enabled user interfaces."""
    task_name = str((task or {}).get("name") or "").strip()
    heading = f"Heartbeat update — {task_name}" if task_name else "Heartbeat update"
    alert = f"{heading}\n\n{message}"

    def persist() -> None:
        with _agent_lock:
            agent.conversation.add_assistant(alert)

    await asyncio.to_thread(persist)
    out = _ui.get("out")
    if out is not None:
        out.put_nowait({"type": "heartbeat", "text": alert, "title": task_name})

    destinations = task or db.get_heartbeat_settings()
    if destinations["notify_telegram"]:
        telegram = db.get_telegram_settings()
        if (
            telegram["enabled"]
            and telegram["token_configured"]
            and telegram["paired"]
        ):
            try:
                sent = await asyncio.to_thread(telegram_service.send_notification, alert)
                if sent:
                    def persist_telegram() -> None:
                        with _agent_lock:
                            telegram_agent.conversation.add_assistant(alert)

                    await asyncio.to_thread(persist_telegram)
            except Exception as exc:
                # A channel outage must never suppress the web notification or
                # turn a successful heartbeat check into a failed run.
                trace.kv("heartbeat telegram", f"delivery failed: {exc}")

    if destinations["notify_whatsapp"]:
        whatsapp = db.get_whatsapp_settings()
        if (
            whatsapp["enabled"]
            and whatsapp["credentials_configured"]
            and whatsapp["paired"]
        ):
            try:
                sent = await asyncio.to_thread(whatsapp_service.send_notification, alert)
                if sent:
                    def persist_whatsapp() -> None:
                        with _agent_lock:
                            whatsapp_agent.conversation.add_assistant(alert)

                    await asyncio.to_thread(persist_whatsapp)
            except Exception as exc:
                trace.kv("heartbeat whatsapp", f"delivery failed: {exc}")


heartbeat_service = HeartbeatService(_deliver_heartbeat_alert)


def _telegram_paired(chat_id: int, name: str, username: str) -> None:
    db.pair_telegram_chat(chat_id, name, username)


def _telegram_status(status: str, bot_username: str, error: str) -> None:
    db.update_telegram_connection(
        status, bot_username=bot_username or None, error=error
    )


_telegram_saved = db.get_telegram_settings(include_secret=True)
telegram_service = TelegramBridge(
    agent=telegram_agent,
    turn_lock=_agent_lock,
    token=_telegram_saved["bot_token"],
    chat_id=_telegram_saved["chat_id"],
    reply_mode=_telegram_saved["reply_mode"],
    on_paired=_telegram_paired,
    on_status=_telegram_status,
)


def _whatsapp_paired(phone: str, name: str) -> None:
    db.pair_whatsapp_phone(phone, name)


def _whatsapp_inbound(phone: str, name: str) -> None:
    db.mark_whatsapp_inbound(phone, name)


_whatsapp_saved = db.get_whatsapp_settings(include_secret=True)
whatsapp_service = WhatsAppBridge(
    agent=whatsapp_agent,
    turn_lock=_agent_lock,
    on_paired=_whatsapp_paired,
    on_inbound=_whatsapp_inbound,
)


def _telegram_public_state() -> dict:
    state = db.get_telegram_settings()
    state["running"] = telegram_service.is_running
    if not state["enabled"]:
        state["connection_status"] = "disabled"
    elif not state["token_configured"]:
        state["connection_status"] = "needs_token"
    elif telegram_service.last_error and not telegram_service.is_running:
        state["connection_status"] = "error"
        state["last_error"] = telegram_service.last_error
    return state


def _apply_telegram_settings() -> dict:
    saved = db.get_telegram_settings(include_secret=True)
    started = telegram_service.reconfigure(
        token=saved["bot_token"],
        chat_id=saved["chat_id"],
        reply_mode=saved["reply_mode"],
        start=bool(saved["enabled"] and saved["bot_token"]),
    )
    if saved["enabled"] and saved["bot_token"] and not started:
        db.update_telegram_connection("error", error=telegram_service.last_error)
    elif not saved["enabled"]:
        db.update_telegram_connection("disabled")
    return _telegram_public_state()


def _safe_telegram_error(exc: Exception, token: str = "") -> str:
    message = str(exc) or exc.__class__.__name__
    if token:
        message = message.replace(token, "[hidden token]")
    return message[:600]


def _whatsapp_public_state() -> dict:
    state = db.get_whatsapp_settings()
    state["webhook_path"] = whatsapp_service.webhook_url_path
    if not state["enabled"]:
        state["connection_status"] = "disabled"
    elif not state["credentials_configured"]:
        state["connection_status"] = "incomplete"
    return state


def _apply_whatsapp_settings() -> dict:
    saved = db.get_whatsapp_settings(include_secret=True)
    whatsapp_service.reconfigure(saved)
    if not saved["enabled"]:
        db.update_whatsapp_connection("disabled")
    elif not saved["credentials_configured"]:
        db.update_whatsapp_connection("incomplete")
    elif saved["connection_status"] in {"disabled", "incomplete"}:
        db.update_whatsapp_connection("configured")
    return _whatsapp_public_state()


def _safe_whatsapp_error(exc: Exception, settings: dict | None = None) -> str:
    message = str(exc) or exc.__class__.__name__
    for secret in (
        str((settings or {}).get("access_token") or ""),
        str((settings or {}).get("app_secret") or ""),
    ):
        if secret:
            message = message.replace(secret, "[hidden credential]")
    return message[:600]


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await heartbeat_service.start()
    saved = db.get_telegram_settings(include_secret=True)
    if saved["enabled"] and saved["bot_token"]:
        if not telegram_service.start_background():
            trace.kv("telegram", f"not started: {telegram_service.last_error}")
    try:
        yield
    finally:
        oauth_tasks = [
            run.task
            for run in _oauth_setup_runs.values()
            if run.task is not None and not run.task.done()
        ]
        for task in oauth_tasks:
            task.cancel()
        if oauth_tasks:
            await asyncio.gather(*oauth_tasks, return_exceptions=True)
        _oauth_setup_runs.clear()
        await asyncio.to_thread(telegram_service.stop)
        await heartbeat_service.stop()


app = FastAPI(title="Mounir", lifespan=_lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.mount("/images", StaticFiles(directory=ROOT_DIR / "images"), name="images")
WEB_DIST_DIR = ROOT_DIR / "web-dist"
if WEB_DIST_DIR.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=WEB_DIST_DIR / "assets"),
        name="web-assets",
    )


def _frontend_response():
    """Serve the compiled React shell or explain how to create it."""
    entry = WEB_DIST_DIR / "index.html"
    if entry.is_file():
        return FileResponse(entry)
    return HTMLResponse(
        "<h1>Frontend build missing</h1>"
        "<p>Run <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>, "
        "then restart Mounir.</p>",
        status_code=503,
    )


@app.get("/")
async def root():
    return _frontend_response()


@app.get("/admin")
async def admin():
    return _frontend_response()


@app.get("/admin/{path:path}")
async def admin_route(path: str):
    """Let React Router handle direct links inside Agent Studio."""
    return _frontend_response()


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
                with tools.use_confirmation_handler(_web_confirm):
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
            with tools.use_confirmation_handler(_web_confirm):
                return "".join(agent.respond(text, voice=True))

    reply = await loop.run_in_executor(None, respond)

    # Synthesize reply to WAV bytes (in-memory, no playback on server side).
    audio_b64 = ""
    try:
        wav_bytes = tts.synthesize_wav(reply)
        if wav_bytes:
            audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
    except Exception:
        pass  # UI will just show text if TTS isn't set up

    return JSONResponse({"text": text, "lang": lang, "reply": reply, "audio_b64": audio_b64})


# --- Admin: models, MCP servers, subagents ------------------------------------

@app.get("/api/voice-settings")
async def get_voice_settings():
    return db.get_voice_settings()


@app.get("/api/tts-voices")
async def get_tts_voices(provider: str, model: str):
    try:
        return tts.discover_voices(provider, model)
    except (OSError, TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.put("/api/voice-settings")
async def update_voice_settings(req: dict):
    try:
        return db.update_voice_settings(stt=req.get("stt"), tts=req.get("tts"))
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

@app.get("/api/profile")
async def get_profile():
    return db.get_profile()


@app.put("/api/profile")
async def update_profile(req: dict):
    try:
        return db.update_profile(**req)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/api/telegram")
async def get_telegram_settings():
    return _telegram_public_state()


@app.put("/api/telegram")
async def update_telegram_settings(req: dict):
    allowed = {"enabled", "bot_token", "reply_mode", "clear_token"}
    fields = {key: value for key, value in req.items() if key in allowed}
    try:
        saved = db.update_telegram_settings(**fields)
        if set(fields) == {"reply_mode"}:
            telegram_service.set_reply_mode(saved["reply_mode"])
            return _telegram_public_state()
        return await asyncio.to_thread(_apply_telegram_settings)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/telegram/test")
async def test_telegram_connection():
    saved = db.get_telegram_settings(include_secret=True)
    token = saved["bot_token"]
    if not token:
        return JSONResponse({"error": "Add a bot token first."}, status_code=400)
    try:
        identity = await asyncio.to_thread(
            telegram_service.test_connection, token, saved["chat_id"]
        )
        if saved["chat_id"] and identity.get("chat"):
            db.pair_telegram_chat(
                int(saved["chat_id"]),
                identity["chat"].get("name", ""),
                identity["chat"].get("username", ""),
            )
        if saved["enabled"] and telegram_service.is_running:
            status = "connected" if saved["chat_id"] else "waiting_pairing"
        else:
            status = "configured"
        db.update_telegram_connection(
            status,
            bot_username=identity["username"],
            tested=True,
        )
        return {**_telegram_public_state(), "bot": identity}
    except Exception as exc:
        message = _safe_telegram_error(exc, token)
        db.update_telegram_connection("error", error=message, tested=True)
        return JSONResponse({"error": message}, status_code=400)


@app.post("/api/telegram/pairing-code")
async def create_telegram_pairing_code():
    saved = db.get_telegram_settings(include_secret=True)
    if not saved["enabled"]:
        return JSONResponse({"error": "Enable Telegram before pairing."}, status_code=400)
    if not saved["bot_token"]:
        return JSONResponse({"error": "Add a bot token before pairing."}, status_code=400)
    if saved["chat_id"]:
        return JSONResponse(
            {"error": "Disconnect the current Telegram account before pairing another one."},
            status_code=409,
        )
    if not telegram_service.is_running:
        state = await asyncio.to_thread(_apply_telegram_settings)
        if not state["running"]:
            return JSONResponse(
                {"error": state.get("last_error") or "Telegram could not start."},
                status_code=400,
            )
    try:
        return telegram_service.create_pairing_code()
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.delete("/api/telegram/pairing")
async def disconnect_telegram_account():
    db.clear_telegram_pairing()
    return await asyncio.to_thread(_apply_telegram_settings)


@app.delete("/api/telegram/token")
async def remove_telegram_token():
    db.update_telegram_settings(clear_token=True)
    return await asyncio.to_thread(_apply_telegram_settings)


@app.get("/api/whatsapp")
async def get_whatsapp_settings():
    return _whatsapp_public_state()


@app.put("/api/whatsapp")
async def update_whatsapp_settings(req: dict):
    allowed = {
        "enabled",
        "access_token",
        "phone_number_id",
        "business_account_id",
        "app_secret",
        "api_version",
        "heartbeat_template_name",
        "heartbeat_template_language",
        "clear_credentials",
        "regenerate_verify_token",
    }
    fields = {key: value for key, value in req.items() if key in allowed}
    try:
        db.update_whatsapp_settings(**fields)
        return _apply_whatsapp_settings()
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/whatsapp/test")
async def test_whatsapp_connection():
    saved = db.get_whatsapp_settings(include_secret=True)
    whatsapp_service.reconfigure(saved)
    error = whatsapp_service.configuration_error()
    if error:
        return JSONResponse({"error": error}, status_code=400)
    try:
        identity = await asyncio.to_thread(whatsapp_service.test_connection)
        status = "connected" if saved["webhook_verified_at"] else "configured"
        db.update_whatsapp_connection(
            status,
            display_phone_number=identity["display_phone_number"],
            verified_name=identity["verified_name"],
            tested=True,
        )
        return {**_whatsapp_public_state(), "account": identity}
    except Exception as exc:
        message = _safe_whatsapp_error(exc, saved)
        db.update_whatsapp_connection("error", error=message, tested=True)
        return JSONResponse({"error": message}, status_code=400)


@app.post("/api/whatsapp/pairing-code")
async def create_whatsapp_pairing_code():
    saved = db.get_whatsapp_settings(include_secret=True)
    if not saved["enabled"]:
        return JSONResponse({"error": "Enable WhatsApp before pairing."}, status_code=400)
    if not saved["credentials_configured"]:
        return JSONResponse(
            {"error": "Complete the WhatsApp Cloud API connection first."},
            status_code=400,
        )
    if not saved["webhook_verified"]:
        return JSONResponse(
            {"error": "Verify the webhook in Meta before pairing a phone."},
            status_code=400,
        )
    if saved["paired_phone"]:
        return JSONResponse(
            {"error": "Disconnect the current WhatsApp phone before pairing another one."},
            status_code=409,
        )
    whatsapp_service.reconfigure(saved)
    try:
        return whatsapp_service.create_pairing_code()
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.delete("/api/whatsapp/pairing")
async def disconnect_whatsapp_phone():
    whatsapp_service.cancel_pairing()
    db.clear_whatsapp_pairing()
    return _apply_whatsapp_settings()


@app.delete("/api/whatsapp/credentials")
async def remove_whatsapp_credentials():
    whatsapp_service.cancel_pairing()
    db.update_whatsapp_settings(clear_credentials=True)
    return _apply_whatsapp_settings()


@app.get("/api/whatsapp/webhook")
async def verify_whatsapp_webhook(request: Request):
    query = request.query_params
    challenge = whatsapp_service.verify_webhook(
        query.get("hub.mode", ""),
        query.get("hub.verify_token", ""),
        query.get("hub.challenge", ""),
    )
    if challenge is None:
        return Response(content="Webhook verification failed", status_code=403)
    db.update_whatsapp_connection("connected", webhook_verified=True)
    return Response(content=challenge, media_type="text/plain")


@app.post("/api/whatsapp/webhook")
async def receive_whatsapp_webhook(
    request: Request, background_tasks: BackgroundTasks
):
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    if not whatsapp_service.verify_signature(body, signature):
        return Response(content="Invalid webhook signature", status_code=403)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Response(content="Invalid webhook payload", status_code=400)
    if payload.get("object") != "whatsapp_business_account":
        return Response(content="Unsupported webhook object", status_code=400)
    background_tasks.add_task(whatsapp_service.handle_webhook, payload)
    return Response(content="EVENT_RECEIVED", media_type="text/plain")


@app.get("/api/heartbeat")
async def get_heartbeat():
    tasks = [
        {
            **task,
            "recent_runs": db.list_heartbeat_task_runs(task["id"]),
        }
        for task in db.list_heartbeat_tasks()
    ]
    return {
        **db.get_heartbeat_settings(),
        "capabilities": db.get_heartbeat_capabilities(),
        "recent_runs": db.list_heartbeat_runs(),
        "tasks": tasks,
    }


def _heartbeat_task_fields(req: dict) -> dict:
    allowed = {
        "name",
        "enabled",
        "interval_minutes",
        "execution_limit",
        "instructions",
        "selected_agents",
        "selected_tools",
        "notify_telegram",
        "notify_whatsapp",
    }
    return {key: value for key, value in req.items() if key in allowed}


@app.post("/api/heartbeat/tasks")
async def create_heartbeat_task(req: dict):
    try:
        task = db.create_heartbeat_task(**_heartbeat_task_fields(req))
        heartbeat_service.wake()
        return {**task, "recent_runs": []}
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.put("/api/heartbeat/tasks/{task_id}")
async def update_heartbeat_task(task_id: int, req: dict):
    try:
        task = db.update_heartbeat_task(task_id, **_heartbeat_task_fields(req))
        if task is None:
            return JSONResponse({"error": "Heartbeat task not found."}, status_code=404)
        heartbeat_service.wake()
        return {
            **task,
            "recent_runs": db.list_heartbeat_task_runs(task_id),
        }
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.delete("/api/heartbeat/tasks/{task_id}")
async def delete_heartbeat_task(task_id: int):
    if not db.delete_heartbeat_task(task_id):
        return JSONResponse({"error": "Heartbeat task not found."}, status_code=404)
    heartbeat_service.wake()
    return {"ok": True}


@app.post("/api/heartbeat/tasks/{task_id}/run")
async def run_heartbeat_task_now(task_id: int):
    if db.get_heartbeat_task(task_id) is None:
        return JSONResponse({"error": "Heartbeat task not found."}, status_code=404)
    try:
        task = await heartbeat_service.run_now("manual", task_id)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return {
        **task,
        "recent_runs": db.list_heartbeat_task_runs(task_id),
    }


@app.get("/api/heartbeat/notifications")
async def get_heartbeat_notifications(unread_only: bool = False):
    return {
        "notifications": db.list_heartbeat_notifications(
            unread_only=unread_only
        )
    }


@app.patch("/api/heartbeat/notifications/{notification_id}/read")
async def mark_heartbeat_notification_read(notification_id: int):
    if not db.mark_heartbeat_notification_read(notification_id):
        return JSONResponse({"error": "Notification not found."}, status_code=404)
    return {"ok": True}


@app.delete("/api/heartbeat/notifications/{notification_id}")
async def delete_heartbeat_notification(notification_id: int):
    if not db.delete_heartbeat_notification(notification_id):
        return JSONResponse({"error": "Notification not found."}, status_code=404)
    return {"ok": True}


@app.put("/api/heartbeat")
async def update_heartbeat(req: dict):
    try:
        requested_enabled = req.get("enabled")
        selected_tools = req.get("selected_tools")
        if requested_enabled is True:
            if selected_tools is None:
                selected_tools = [
                    {"agent_key": agent["key"], "tool_name": tool["name"]}
                    for agent in db.get_heartbeat_capabilities()
                    for tool in agent["tools"]
                    if tool["selected"] and not tool["requires_confirmation"]
                ]
            if not selected_tools:
                raise ValueError(
                    "select at least one non-interactive tool before enabling heartbeat"
                )
        db.update_heartbeat_settings(
            enabled=requested_enabled,
            interval_minutes=req.get("interval_minutes"),
            instructions=req.get("instructions"),
            selected_tools=selected_tools,
            notify_telegram=req.get("notify_telegram"),
            notify_whatsapp=req.get("notify_whatsapp"),
        )
        heartbeat_service.wake()
        return await get_heartbeat()
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/heartbeat/run")
async def run_heartbeat_now():
    try:
        await heartbeat_service.run_now("manual")
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return await get_heartbeat()

@app.get("/api/agent-overview")
async def agent_overview():
    """Return the configured, user-visible agent topology for Agent Studio."""
    supervisor_tools = []
    for registered_tool in tools.GENERAL_TOOLS:
        supervisor_tools.append(
            {
                "name": registered_tool.name,
                "description": registered_tool.description,
            }
        )

    profile = db.get_profile()
    supervisor = db.get_supervisor_config()
    return {
        "supervisor": {
            **supervisor,
            "name": profile["assistant_name"],
            "description": (
                "Understands your request, uses local computer tools, and "
                "coordinates the right specialist for focused work."
            ),
            "tools": supervisor_tools,
        },
        "builtins": db.list_builtin_agents(),
    }


@app.put("/api/supervisor")
async def update_supervisor(req: dict):
    try:
        db.update_supervisor_model(req.get("model_id"))
        return (await agent_overview())["supervisor"]
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.put("/api/builtin-agents/{agent_key}")
async def update_builtin_agent(agent_key: str, req: dict):
    try:
        return db.update_builtin_agent(
            agent_key,
            model_id=req.get("model_id") if "model_id" in req else None,
            enabled=req.get("enabled") if "enabled" in req else None,
        )
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

@app.get("/api/models")
async def list_models():
    return [db.model_for_api(model) for model in db.list_models()]


@app.post("/api/models")
async def create_model(req: dict):
    try:
        model = db.add_model(
            req.get("name", ""),
            req.get("model", ""),
            req.get("provider", ""),
            req.get("base_url", ""),
            req.get("api_key", ""),
        )
        return db.model_for_api(model)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.put("/api/models/{model_id}")
async def update_model(model_id: int, req: dict):
    try:
        m = db.update_model(model_id, **req)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not m:
        return JSONResponse({"error": "Model not found."}, status_code=404)
    return db.model_for_api(m)


def _restricted_delete_response(result: db.DeletionResult, resource: str):
    """Translate a repository delete outcome into one consistent API response."""
    if result.deleted:
        return JSONResponse({"ok": True})
    if result.status == "not_found":
        return JSONResponse({"error": f"{resource} not found."}, status_code=404)
    used_by = ", ".join(result.dependencies)
    return JSONResponse(
        {
            "error": f"This {resource.lower()} cannot be deleted because it is used by {used_by}.",
            "dependencies": result.dependencies,
        },
        status_code=409,
    )


@app.delete("/api/models/{model_id}")
async def delete_model(model_id: int):
    return _restricted_delete_response(db.delete_model_result(model_id), "Model")


@app.get("/api/mcp-servers")
async def list_servers():
    return [db.server_for_api(server) for server in db.list_servers()]


def _server_file_changes(req: dict) -> tuple[list[dict], list[str]]:
    uploads = []
    raw_uploads = req.pop("credential_files", []) or []
    removals = req.pop("remove_credential_files", []) or []
    if not isinstance(raw_uploads, list) or not isinstance(removals, list):
        raise ValueError("Credential file changes must be lists.")
    for item in raw_uploads:
        if not isinstance(item, dict):
            raise ValueError("Credential file entry is invalid.")
        encoded = str(item.get("content") or "")
        if encoded.startswith("data:"):
            encoded = encoded.partition(",")[2]
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Credential file content is invalid.") from exc
        uploads.append(
            {
                "env_var": item.get("env_var"),
                "filename": item.get("filename"),
                "content": content,
            }
        )
    return uploads, [str(value) for value in removals]


async def _run_oauth_setup(server_id: int, run: _OAuthSetupRun) -> None:
    async def redirect_handler(url: str) -> None:
        run.authorization_url = url
        run.status = "waiting_for_authorization"
        run.redirect_ready.set()

    async def callback_handler() -> tuple[str, str | None]:
        return await asyncio.wait_for(run.callback.get(), timeout=300)

    try:
        spec = db.build_server_spec(server_id)
        if spec is None:
            raise ValueError("Server not found.")
        spec["oauth_auth"] = mcp_oauth.provider_for_spec(
            spec,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        tools_found = await asyncio.wait_for(discover_tools(spec), timeout=330)
        db.save_server_tools(server_id, tools_found)
        run.status = "connected"
    except Exception as exc:
        from mounir.specialists.mcp_agent import _exc_detail

        run.error = _exc_detail(exc)
        run.status = "failed"
        db.record_server_test_failure(server_id, run.error)
    finally:
        run.redirect_ready.set()


async def _run_local_setup_command(server_id: int) -> dict:
    spec = db.build_server_spec(server_id)
    if spec is None:
        raise ValueError("Server not found.")
    command = str(spec.get("setup_command") or "").strip()
    if not command:
        raise ValueError("No setup command is configured.")
    if spec.get("transport") != "stdio":
        raise ValueError("Setup commands are available only for local MCP servers.")
    argv = shlex.split(command)
    if not argv:
        raise ValueError("The setup command is empty.")
    environment = os.environ.copy()
    environment.update({str(key): str(value) for key, value in spec.get("env", {}).items()})
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise ValueError(f"Could not start the setup command: {exc}") from exc
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=300)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise ValueError("The setup command timed out after 5 minutes.")
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
            await process.communicate()
        raise
    message = output.decode(errors="replace").strip()[-4000:]
    if process.returncode:
        raise ValueError(message or f"Setup command exited with code {process.returncode}.")
    return {"ok": True, "message": message or "Setup completed successfully."}


@app.post("/api/mcp-servers")
async def create_server(req: dict):
    created_id: int | None = None
    try:
        req = dict(req)
        uploads, removals = _server_file_changes(req)
        server = db.add_server(
            req.get("name", ""),
            req.get("connection", ""),
            transport=req.get("transport", "stdio"),
            headers=req.get("headers", "{}"),
            env=req.get("env", "{}"),
            description=req.get("description", ""),
            auth_scheme=req.get("auth_scheme", ""),
            setup_command=req.get("setup_command", ""),
        )
        created_id = int(server["id"])
        if uploads or removals:
            db.replace_server_files(server["id"], uploads, removals)
            server = db.get_server(server["id"])
        return db.server_for_api(server)
    except (TypeError, ValueError) as exc:
        if created_id is not None:
            db.delete_server_result(created_id)
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.put("/api/mcp-servers/{server_id}")
async def update_server(server_id: int, req: dict):
    try:
        req = dict(req)
        uploads, removals = _server_file_changes(req)
        s = db.update_server(server_id, **req)
        if s and (uploads or removals):
            db.replace_server_files(server_id, uploads, removals)
            s = db.get_server(server_id)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not s:
        return JSONResponse({"error": "Server not found."}, status_code=404)
    return db.server_for_api(s)


@app.get("/api/mcp-servers/{server_id}/tools")
async def cached_server_tools(server_id: int):
    state = db.get_server_tools_state(server_id)
    if state is None:
        return JSONResponse({"error": "Server not found."}, status_code=404)
    return state


@app.post("/api/mcp-servers/{server_id}/test")
async def test_server(server_id: int):
    spec = db.build_server_spec(server_id)
    if spec is None:
        return JSONResponse({"error": "Server not found."}, status_code=404)
    if spec.get("auth_scheme") == "oauth" and not mcp_oauth.oauth_state_is_valid(server_id):
        return JSONResponse(
            {"error": "Connect OAuth from the setup section before testing this server."},
            status_code=409,
        )
    try:
        tools_found = await asyncio.wait_for(discover_tools(spec), timeout=45)
    except TimeoutError:
        error = "Connection timed out after 45 seconds."
        db.record_server_test_failure(server_id, error)
        return JSONResponse(
            {"error": error}, status_code=504
        )
    except Exception as exc:
        # AnyIO transports may wrap connection failures in an ExceptionGroup.
        from mounir.specialists.mcp_agent import _exc_detail

        error = _exc_detail(exc)
        db.record_server_test_failure(server_id, error)
        return JSONResponse({"error": error}, status_code=400)
    state = db.save_server_tools(server_id, tools_found)
    return {"ok": True, **state}


@app.get("/api/mcp-servers/{server_id}/setup")
async def server_setup_status(server_id: int):
    server = db.get_server(server_id)
    if server is None:
        return JSONResponse({"error": "Server not found."}, status_code=404)
    run = _oauth_setup_runs.get(server_id)
    oauth_enabled = server.get("auth_scheme") == "oauth"
    oauth_connected = mcp_oauth.oauth_state_is_valid(server_id) if oauth_enabled else False
    if run and run.status in {"starting", "waiting_for_authorization"}:
        status = {"kind": "waiting", "text": "Waiting for authorization"}
    elif run and run.status == "failed":
        status = {"kind": "failed", "text": "Authorization failed"}
    elif oauth_connected:
        status = {"kind": "connected", "text": "OAuth connected"}
    elif oauth_enabled:
        status = {"kind": "ready", "text": "Authorization required"}
    elif server.get("setup_command"):
        status = {"kind": "ready", "text": "Setup command ready"}
    else:
        status = {"kind": "ready", "text": "Ready to test"}
    return {
        "configured": bool(
            oauth_enabled
            or server.get("setup_command")
            or db.list_server_files(server_id)
        ),
        "status": status,
        "oauth": {
            "enabled": oauth_enabled,
            "connected": oauth_connected,
            "in_progress": bool(
                run and run.status in {"starting", "waiting_for_authorization"}
            ),
        },
        "command": {"configured": bool(server.get("setup_command"))},
        "credential_files": db.list_server_files(server_id),
        "error": run.error if run else "",
    }


@app.post("/api/mcp-servers/{server_id}/setup/files/{action_id}")
async def run_server_setup_file_action(
    server_id: int, action_id: str, file: UploadFile = File(...)
):
    return JSONResponse(
        {"error": "Credential files are managed from the server configuration."},
        status_code=404,
    )


@app.post("/api/mcp-servers/{server_id}/setup/actions/{action_id}")
async def run_server_setup_action(server_id: int, action_id: str, request: Request):
    server = db.get_server(server_id)
    if server is None:
        return JSONResponse({"error": "Server not found."}, status_code=404)
    try:
        if action_id == "run_command":
            return await _run_local_setup_command(server_id)
        if action_id == "disconnect_oauth":
            run = _oauth_setup_runs.pop(server_id, None)
            if run and run.task and not run.task.done():
                run.task.cancel()
            db.clear_server_oauth(server_id)
            return {"ok": True, "message": "OAuth disconnected."}
        if action_id == "authorize_oauth":
            if server.get("transport") == "stdio" or server.get("auth_scheme") != "oauth":
                raise ValueError("OAuth is not enabled for this server.")
            previous = _oauth_setup_runs.get(server_id)
            if previous and previous.task and not previous.task.done():
                return {
                    "ok": True,
                    "authorization_url": previous.authorization_url,
                    "message": "Authorization is already in progress.",
                }
            redirect_uri = str(
                request.url_for("server_oauth_callback", server_id=str(server_id))
            )
            db.clear_server_oauth_tokens(server_id)
            db.prepare_server_oauth(server_id, redirect_uri)
            run = _OAuthSetupRun()
            _oauth_setup_runs[server_id] = run
            run.task = asyncio.create_task(_run_oauth_setup(server_id, run))
            await asyncio.wait_for(run.redirect_ready.wait(), timeout=45)
            if run.error and not run.authorization_url:
                raise ValueError(run.error)
            return {
                "ok": True,
                "authorization_url": run.authorization_url,
                "message": "Continue authorization in the new browser window.",
            }
        return JSONResponse({"error": "Setup action not found."}, status_code=404)
    except TimeoutError:
        return JSONResponse(
            {"error": "The MCP server did not start authorization in time."},
            status_code=504,
        )
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/api/mcp-servers/{server_id}/oauth/callback", name="server_oauth_callback")
async def server_oauth_callback(server_id: int, request: Request):
    run = _oauth_setup_runs.get(server_id)
    if run is None or run.task is None or run.task.done():
        return HTMLResponse(
            "<h2>Authorization session expired</h2><p>Return to Mounir and try again.</p>",
            status_code=410,
        )
    code = request.query_params.get("code", "")
    state = request.query_params.get("state")
    error = request.query_params.get("error", "")
    if error:
        run.error = request.query_params.get("error_description") or error
    if run.callback.empty():
        await run.callback.put((code, state))
    return HTMLResponse(
        """
        <!doctype html><html><head><title>Mounir authorization</title></head>
        <body style="font-family:system-ui;background:#0d1411;color:#e7eee9;padding:40px">
        <h2>Authorization received</h2>
        <p>You can close this window and return to Mounir.</p>
        <script>window.setTimeout(() => window.close(), 900)</script>
        </body></html>
        """
    )


@app.delete("/api/mcp-servers/{server_id}")
async def delete_server(server_id: int):
    return _restricted_delete_response(
        db.delete_server_result(server_id), "MCP server"
    )


@app.get("/api/subagents")
async def list_subagents():
    return [db.subagent_for_api(agent) for agent in db.list_subagents()]


@app.get("/api/subagent-nodes")
async def list_subagent_nodes():
    return db.list_subagent_nodes()


@app.post("/api/subagent-nodes")
async def create_subagent_node(req: dict):
    try:
        return db.add_subagent_node(
            req.get("subagent_id"), req.get("parent_node_id")
        )
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/api/subagent-nodes/{node_id}")
async def get_subagent_node(node_id: int):
    node = db.get_subagent_node(node_id)
    if node is None:
        return JSONResponse({"error": "Subagent node not found."}, status_code=404)
    return node


@app.put("/api/subagent-nodes/{node_id}")
async def update_subagent_node(node_id: int, req: dict):
    if "enabled_tools" not in req:
        return JSONResponse(
            {"error": "Provide enabled_tools for this subagent node."},
            status_code=400,
        )
    try:
        node = db.update_subagent_node(node_id, enabled_tools=req["enabled_tools"])
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if node is None:
        return JSONResponse({"error": "Subagent node not found."}, status_code=404)
    return node


@app.delete("/api/subagent-nodes/{node_id}")
async def remove_subagent_node(node_id: int):
    try:
        result = db.remove_subagent_node(node_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if result is None:
        return JSONResponse({"error": "Subagent node not found."}, status_code=404)
    return result


def _decode_subagent_icon(value) -> dict:
    """Validate a small browser data URL and return DB-ready image fields."""
    if value in (None, ""):
        return {"icon_data": b"", "icon_mime": ""}
    if not isinstance(value, str) or not value.startswith("data:image/"):
        raise ValueError("Choose a PNG, JPEG, WebP, or GIF image.")
    header, separator, encoded = value.partition(",")
    if not separator or not header.endswith(";base64"):
        raise ValueError("The selected icon could not be read.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("The selected icon could not be read.") from exc
    if not raw:
        raise ValueError("The selected icon is empty.")
    if len(raw) > SUBAGENT_ICON_MAX_BYTES:
        raise ValueError("The icon must be smaller than 512 KB.")

    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime = "image/webp"
    elif raw.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    else:
        raise ValueError("Choose a valid PNG, JPEG, WebP, or GIF image.")
    return {"icon_data": raw, "icon_mime": mime}


@app.get("/api/subagents/{subagent_id}/icon")
async def get_subagent_icon(subagent_id: int):
    icon = db.get_subagent_icon(subagent_id)
    if icon is None:
        return JSONResponse({"error": "Subagent icon not found."}, status_code=404)
    data, mime = icon
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/subagents")
async def create_subagent(req: dict):
    try:
        mcp_agents._validate_agent_name(req.get("name", ""))
        icon = _decode_subagent_icon(req.get("icon_data")) if "icon_data" in req else {}
        subagent = db.add_subagent(
            req.get("name", ""),
            req.get("description", ""),
            req.get("system_prompt", ""),
            req.get("model_id"),
            req.get("mcp_server_id"),
            confirm_tool_calls=req.get("confirm_tool_calls", True),
            parent_agent_id=req.get("parent_agent_id"),
            confirm_tools=req.get("confirm_tools"),
            dedupe_tools=req.get("dedupe_tools"),
            enabled=req.get("enabled", True),
            enabled_tools=req.get("enabled_tools"),
            parent_node_id=req.get("parent_node_id"),
            connect_to_workflow=req.get("connect_to_workflow", True),
            **icon,
        )
        return db.subagent_for_api(subagent)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.put("/api/subagents/{subagent_id}")
async def update_subagent(subagent_id: int, req: dict):
    try:
        fields = dict(req)
        if "name" in fields:
            mcp_agents._validate_agent_name(fields["name"], exclude_id=subagent_id)
        fields.pop("icon_mime", None)
        if "icon_data" in fields:
            fields.update(_decode_subagent_icon(fields.pop("icon_data")))
        s = db.update_subagent(subagent_id, **fields)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not s:
        return JSONResponse({"error": "Subagent not found."}, status_code=404)
    return db.subagent_for_api(s)


@app.delete("/api/subagents/{subagent_id}")
async def delete_subagent(subagent_id: int):
    try:
        if db.delete_subagent(subagent_id):
            return JSONResponse({"ok": True})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
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
