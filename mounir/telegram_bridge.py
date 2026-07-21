"""Reusable Telegram transport for Mounir.

The FastAPI server owns this service in production.  ``telegram_cli.py`` uses
the same class as a standalone compatibility entry point.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import telebot

from . import config, db, tools, trace
from .agent import Agent

MAX_MESSAGE_CHARS = 4096
CONFIRM_TIMEOUT_SECONDS = 120


class TelegramBridge:
    """Long-poll Telegram on the current machine without exposing a port."""

    def __init__(
        self,
        *,
        agent: Agent | None = None,
        turn_lock: threading.Lock | None = None,
        token: str | None = None,
        chat_id: str | None = None,
        confirm_timeout: float = CONFIRM_TIMEOUT_SECONDS,
        bot_factory: Callable[[str], telebot.TeleBot] = telebot.TeleBot,
    ) -> None:
        self.agent = agent or Agent()
        self.turn_lock = turn_lock or threading.Lock()
        self.token = config.TELEGRAM_BOT_TOKEN if token is None else token
        self.chat_id = config.TELEGRAM_CHAT_ID if chat_id is None else chat_id
        self.confirm_timeout = confirm_timeout
        self._bot_factory = bot_factory
        self.bot: telebot.TeleBot | None = None
        self.username = ""
        self.last_error = ""
        self._thread: threading.Thread | None = None
        self._confirm_lock = threading.Lock()
        self._confirm_event: threading.Event | None = None
        self._confirm_answer = False

    @property
    def configured(self) -> bool:
        return bool(self.token.strip())

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _allowed_chat(self) -> int | None:
        value = self.chat_id.strip()
        return int(value) if value else None

    def configuration_error(self) -> str:
        if not self.configured:
            return "TELEGRAM_BOT_TOKEN is not set"
        try:
            self._allowed_chat()
        except ValueError:
            return "TELEGRAM_CHAT_ID must be a numeric Telegram chat id"
        return ""

    def _ensure_bot(self) -> telebot.TeleBot:
        if self.bot is not None:
            return self.bot
        bot = self._bot_factory(self.token)
        bot.register_message_handler(self._handle_text, content_types=["text"])
        bot.register_message_handler(
            self._handle_other,
            content_types=[
                "photo", "voice", "audio", "video", "document", "sticker", "location"
            ],
        )
        self.bot = bot
        return bot

    @staticmethod
    def split_message(text: str) -> list[str]:
        """Split a reply at Telegram's message limit, preferring line breaks."""
        chunks: list[str] = []
        while len(text) > MAX_MESSAGE_CHARS:
            cut = text.rfind("\n", 0, MAX_MESSAGE_CHARS)
            if cut < MAX_MESSAGE_CHARS // 2:
                cut = MAX_MESSAGE_CHARS
            chunks.append(text[:cut])
            text = text[cut:].lstrip("\n")
        if text:
            chunks.append(text)
        return chunks

    def _send(self, chat_id: int, text: str) -> None:
        bot = self._ensure_bot()
        for chunk in self.split_message(text):
            try:
                bot.send_message(chat_id, chunk, parse_mode="Markdown")
            except telebot.apihelper.ApiTelegramException:
                bot.send_message(chat_id, chunk)

    def _telegram_confirm(self, action: str) -> bool:
        chat_id = self._allowed_chat()
        if chat_id is None:
            return False
        event = threading.Event()
        with self._confirm_lock:
            self._confirm_event = event
            self._confirm_answer = False
        self._send(chat_id, f'⚠ Needs your OK:\n{action}\n\nReply "yes" to approve.')
        answered = event.wait(self.confirm_timeout)
        with self._confirm_lock:
            answer = self._confirm_answer
            if self._confirm_event is event:
                self._confirm_event = None
        if not answered:
            self._send(chat_id, "No answer — cancelled.")
            return False
        return answer

    def _keep_typing(self, chat_id: int, done: threading.Event) -> None:
        bot = self._ensure_bot()
        while not done.is_set():
            try:
                bot.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            done.wait(4)

    def _handle_text(self, message) -> None:
        chat_id = message.chat.id
        allowed = self._allowed_chat()
        bot = self._ensure_bot()

        if allowed is None:
            bot.reply_to(
                message,
                f"This chat's id is {chat_id}.\n"
                f'Set TELEGRAM_CHAT_ID="{chat_id}" and restart Mounir to pair it.',
            )
            return
        if chat_id != allowed:
            bot.reply_to(message, "Sorry, this is a private assistant.")
            return

        text = (message.text or "").strip()
        if not text:
            return

        with self._confirm_lock:
            pending = self._confirm_event
            if pending is not None:
                self._confirm_answer = text.lower() in ("y", "yes")
                pending.set()
                return

        if text == "/start":
            self._send(chat_id, f"{db.get_profile()['assistant_name']} here. Say the word.")
            return
        if text == "/reset":
            with self.turn_lock:
                self.agent.conversation.reset()
            self._send(chat_id, "Conversation cleared.")
            return

        with self.turn_lock:
            trace.node("telegram")
            trace.event(f"← {text[:120]}")
            done = threading.Event()
            typing = threading.Thread(
                target=self._keep_typing, args=(chat_id, done), daemon=True
            )
            typing.start()
            try:
                with tools.use_confirmation_handler(self._telegram_confirm):
                    reply = "".join(self.agent.respond(text)).strip()
            except Exception as exc:
                reply = f"[error] {exc}"
            finally:
                done.set()
                typing.join(timeout=1)
        self._send(chat_id, reply or "(no reply)")
        trace.event(f"→ {len(reply)} chars")

    def _handle_other(self, message) -> None:
        if self._allowed_chat() == message.chat.id:
            self._ensure_bot().reply_to(message, "I can only read text here for now.")

    def _poll(self) -> None:
        bot = self._ensure_bot()
        try:
            try:
                me = bot.get_me()
                self.username = getattr(me, "username", "") or ""
                trace.kv("telegram", f"@{self.username} (long polling)")
                paired = self._allowed_chat()
                trace.kv("paired chat", str(paired) if paired else "not paired yet")
            except Exception as exc:
                # infinity_polling owns reconnection; a temporary startup network
                # failure must not take down the web server.
                self.last_error = str(exc)
                trace.kv("telegram", "connecting in background")
            bot.infinity_polling(timeout=20, long_polling_timeout=20)
        except Exception as exc:
            self.last_error = str(exc)
            trace.kv("telegram", f"stopped: {exc}")

    def start_background(self) -> bool:
        """Start polling on one daemon thread; safe to call more than once."""
        error = self.configuration_error()
        if error:
            self.last_error = error
            return False
        if self.is_running:
            return True
        try:
            self._ensure_bot()
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self._thread = threading.Thread(
            target=self._poll, name="mounir-telegram", daemon=True
        )
        self._thread.start()
        return True

    def run_forever(self) -> bool:
        """Run polling in the current thread for ``telegram_cli.py``."""
        error = self.configuration_error()
        if error:
            self.last_error = error
            return False
        try:
            self._ensure_bot()
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self._poll()
        return True

    def stop(self) -> None:
        """Stop polling and release any confirmation currently waiting."""
        with self._confirm_lock:
            if self._confirm_event is not None:
                self._confirm_answer = False
                self._confirm_event.set()
                self._confirm_event = None
        bot = self.bot
        thread = self._thread
        if bot is not None:
            bot.stop_polling()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
        if thread and thread.is_alive():
            self.last_error = "Telegram polling did not stop within 5 seconds"
            return
        self._thread = None
        self.bot = None
