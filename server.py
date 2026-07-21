"""
FastAPI backend for the Mounir web UI.

Serves:
  - GET  /                 -> the dashboard (index.html)
  - GET  /api/stats        -> live CPU/RAM/network stats (psutil)
  - WS   /ws/chat          -> text chat, streams Mounir's reply token by token
  - POST /api/voice        -> upload audio, returns transcript + spoken reply (base64 wav)

Also owns the heartbeat scheduler and, when configured, the Telegram
long-polling bridge. All user interfaces share one serialized Agent instance.

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
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mounir.agent import Agent
from mounir import config as cfg, db, llm, trace
from mounir import mcp_agents
from mounir import stt, tts, audio as audio_mod, tools
from mounir.heartbeat import HeartbeatService
from mounir.specialists.mcp_agent import discover_tools
from mounir.telegram_bridge import TelegramBridge

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
GMAIL_AUTH_DIR = Path.home() / ".gmail-mcp"
GMAIL_OAUTH_KEYS = GMAIL_AUTH_DIR / "gcp-oauth.keys.json"
GMAIL_CREDENTIALS = GMAIL_AUTH_DIR / "credentials.json"
SUBAGENT_ICON_MAX_BYTES = 512 * 1024


def _secure_gmail_auth_files() -> None:
    """Keep Google OAuth material private even if an older setup wrote it loosely."""
    try:
        if GMAIL_AUTH_DIR.exists():
            GMAIL_AUTH_DIR.chmod(0o700)
        for path in (GMAIL_OAUTH_KEYS, GMAIL_CREDENTIALS):
            if path.exists():
                path.chmod(0o600)
    except OSError:
        # Status/connect endpoints will still report the underlying file error.
        pass


_secure_gmail_auth_files()

# Ensure the SQLite DB exists and the legacy JSON file is migrated.
db.init()

# One shared agent instance = one shared conversation memory across the UI.
agent = Agent()
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


async def _deliver_heartbeat_alert(message: str) -> None:
    """Persist one proactive alert and deliver it to the live dashboard."""
    alert = f"Heartbeat update\n\n{message}"

    def persist() -> None:
        with _agent_lock:
            agent.conversation.add_assistant(alert)

    await asyncio.to_thread(persist)
    out = _ui.get("out")
    if out is not None:
        out.put_nowait({"type": "heartbeat", "text": alert})


heartbeat_service = HeartbeatService(_deliver_heartbeat_alert)


def _telegram_paired(chat_id: int, name: str, username: str) -> None:
    db.pair_telegram_chat(chat_id, name, username)


def _telegram_status(status: str, bot_username: str, error: str) -> None:
    db.update_telegram_connection(
        status, bot_username=bot_username or None, error=error
    )


_telegram_saved = db.get_telegram_settings(include_secret=True)
telegram_service = TelegramBridge(
    agent=agent,
    turn_lock=_agent_lock,
    token=_telegram_saved["bot_token"],
    chat_id=_telegram_saved["chat_id"],
    on_paired=_telegram_paired,
    on_status=_telegram_status,
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
        await asyncio.to_thread(telegram_service.stop)
        await heartbeat_service.stop()


app = FastAPI(title="Mounir", lifespan=_lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.mount("/images", StaticFiles(directory=ROOT_DIR / "images"), name="images")

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
    allowed = {"enabled", "bot_token", "clear_token"}
    fields = {key: value for key, value in req.items() if key in allowed}
    try:
        db.update_telegram_settings(**fields)
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


@app.get("/api/heartbeat")
async def get_heartbeat():
    return {
        **db.get_heartbeat_settings(),
        "capabilities": db.get_heartbeat_capabilities(),
        "recent_runs": db.list_heartbeat_runs(),
    }


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
    for schema in tools.SCHEMAS:
        function = schema.get("function") or {}
        name = str(function.get("name") or "").strip()
        if name and not name.startswith("delegate_to_"):
            supervisor_tools.append(
                {
                    "name": name,
                    "description": str(function.get("description") or ""),
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
        return db.update_builtin_agent_model(agent_key, req.get("model_id"))
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

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
            description=req.get("description", ""),
            auth_scheme=req.get("auth_scheme", ""),
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


def _server_setup_type(server: dict | None) -> str:
    """Return saved onboarding capability; never guess it from a command or URL."""
    return str((server or {}).get("setup_type") or "").strip()


def _gmail_auth_state() -> dict:
    """Describe saved OAuth state without exposing any token or client value."""
    oauth_keys = GMAIL_OAUTH_KEYS.exists()
    credentials_saved = GMAIL_CREDENTIALS.exists()
    expired = False
    invalid = False
    oauth_file_changed = False
    refresh_expires_at = None
    refresh_lifetime_days = None

    if credentials_saved:
        try:
            credentials = json.loads(GMAIL_CREDENTIALS.read_text(encoding="utf-8"))
            if not isinstance(credentials, dict) or not credentials.get("refresh_token"):
                invalid = True
            lifetime = credentials.get("refresh_token_expires_in")
            if isinstance(lifetime, (int, float)) and lifetime > 0:
                refresh_lifetime_days = round(float(lifetime) / 86_400)
                refresh_expires_at = GMAIL_CREDENTIALS.stat().st_mtime + float(lifetime)
                expired = time.time() >= refresh_expires_at
            if oauth_keys:
                oauth_file_changed = (
                    GMAIL_OAUTH_KEYS.stat().st_mtime
                    > GMAIL_CREDENTIALS.stat().st_mtime + 1
                )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            invalid = True

    connected = bool(
        oauth_keys
        and credentials_saved
        and not expired
        and not invalid
        and not oauth_file_changed
    )
    return {
        "oauth_keys": oauth_keys,
        "credentials_saved": credentials_saved,
        "connected": connected,
        "expired": expired,
        "invalid": invalid,
        "oauth_file_changed": oauth_file_changed,
        "refresh_expires_at": refresh_expires_at,
        "refresh_lifetime_days": refresh_lifetime_days,
    }


def _gmail_setup_view() -> dict:
    """Build a UI-neutral setup descriptor for the bundled OAuth adapter."""
    state = _gmail_auth_state()
    if state["expired"]:
        status = {"text": "Authorization expired", "kind": "error"}
        description = (
            "This Google authorization expired after seven days. Reconnect it, "
            "then set the OAuth app publishing status to In production to avoid "
            "the Testing-mode limit."
            if state["refresh_lifetime_days"] == 7
            else "The saved Google authorization expired. Reconnect the account."
        )
    elif state["invalid"]:
        status = {"text": "Authorization is invalid", "kind": "error"}
        description = (
            "The saved authorization is incomplete or damaged. Reconnect the "
            "account to replace it."
        )
    elif state["oauth_file_changed"]:
        status = {"text": "Reconnection required", "kind": ""}
        description = (
            "The OAuth file changed. Reconnect the account to authorize this client."
        )
    elif state["connected"]:
        status = {"text": "Ready", "kind": "ok"}
        description = (
            "Authorization is saved locally. Reconnect if the provider expires "
            "or revokes it."
        )
    elif state["oauth_keys"]:
        status = {"text": "Ready to connect", "kind": ""}
        description = "The OAuth client file is ready. Connect the account."
    else:
        status = {"text": "OAuth file required", "kind": ""}
        description = (
            "Choose the OAuth client JSON supplied by the provider, then connect "
            "the account in your browser. Files stay on this computer."
        )

    return {
        "title": "Gmail account",
        "description": description,
        "status": status,
        "file_actions": [
            {
                "id": "oauth_file",
                "label": (
                    "Replace OAuth file"
                    if state["oauth_keys"]
                    else "Choose OAuth file"
                ),
                "accept": "application/json,.json",
                "busy_label": "Uploading…",
            }
        ],
        "actions": [
            {
                "id": "connect",
                "label": (
                    "Reconnect account"
                    if state["credentials_saved"]
                    else "Connect account"
                ),
                "busy_label": "Waiting for browser…",
                "disabled": not state["oauth_keys"],
                "style": "primary",
            }
        ],
    }


@app.get("/api/mcp-servers/{server_id}/setup")
async def server_setup_status(server_id: int):
    server = db.get_server(server_id)
    setup_type = _server_setup_type(server)
    if setup_type == "gmail_oauth":
        return _gmail_setup_view()
    return JSONResponse(
        {"error": "This server has no additional setup actions."}, status_code=404
    )


@app.post("/api/mcp-servers/{server_id}/setup/files/{action_id}")
async def run_server_setup_file_action(
    server_id: int, action_id: str, file: UploadFile = File(...)
):
    server = db.get_server(server_id)
    if _server_setup_type(server) != "gmail_oauth" or action_id != "oauth_file":
        return JSONResponse({"error": "Setup file action not found."}, status_code=404)
    raw = await file.read(65_537)
    if len(raw) > 65_536:
        return JSONResponse({"error": "OAuth key file is too large."}, status_code=400)
    try:
        payload = json.loads(raw.decode("utf-8"))
        oauth = payload.get("installed") or payload.get("web")
        if (
            not isinstance(oauth, dict)
            or not oauth.get("client_id")
            or not oauth.get("client_secret")
        ):
            raise ValueError
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
        return JSONResponse(
            {"error": "Choose the OAuth client JSON downloaded from Google Cloud."},
            status_code=400,
        )
    GMAIL_AUTH_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    GMAIL_OAUTH_KEYS.write_bytes(raw)
    _secure_gmail_auth_files()
    return {"ok": True, "setup": _gmail_setup_view()}


@app.post("/api/mcp-servers/{server_id}/setup/actions/{action_id}")
async def run_server_setup_action(server_id: int, action_id: str):
    spec = db.build_server_spec(server_id)
    server = db.get_server(server_id)
    if (
        _server_setup_type(server) != "gmail_oauth"
        or action_id != "connect"
        or spec is None
    ):
        return JSONResponse({"error": "Setup action not found."}, status_code=404)
    if not GMAIL_OAUTH_KEYS.exists():
        return JSONResponse(
            {"error": "Upload the Google OAuth client JSON first."}, status_code=400
        )
    argv = shlex.split(spec["connection"])
    if not argv:
        return JSONResponse({"error": "The Gmail server command is empty."}, status_code=400)
    env = os.environ.copy()
    env.update(spec.get("env") or {})
    _secure_gmail_auth_files()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            "auth",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
    except TimeoutError:
        process.terminate()
        await process.wait()
        return JSONResponse(
            {"error": "Gmail connection timed out before authorization completed."},
            status_code=504,
        )
    except Exception as exc:
        return JSONResponse(
            {"error": f"Could not start Gmail authorization: {exc}"},
            status_code=400,
        )

    if process.returncode != 0 or not GMAIL_CREDENTIALS.exists():
        detail = (stderr or stdout).decode(errors="replace").strip()[-700:]
        return JSONResponse(
            {"error": detail or "Gmail authorization did not complete."}, status_code=400
        )
    _secure_gmail_auth_files()
    state = _gmail_auth_state()
    if not state["connected"]:
        return JSONResponse(
            {"error": "Gmail saved credentials, but they are not usable."},
            status_code=400,
        )
    return {"ok": True, "setup": _gmail_setup_view()}


@app.delete("/api/mcp-servers/{server_id}")
async def delete_server(server_id: int):
    if db.delete_server(server_id):
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Server not found or in use."}, status_code=404)


@app.get("/api/subagents")
async def list_subagents():
    return db.list_subagents()


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
        return db.add_subagent(
            req.get("name", ""),
            req.get("description", ""),
            req.get("system_prompt", ""),
            int(req.get("model_id", 0)),
            int(req.get("mcp_server_id", 0)),
            confirm_tool_calls=req.get("confirm_tool_calls", True),
            parent="supervisor",
            confirm_tools=req.get("confirm_tools"),
            dedupe_tools=req.get("dedupe_tools"),
            **icon,
        )
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
