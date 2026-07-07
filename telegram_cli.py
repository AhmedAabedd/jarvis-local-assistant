#!/usr/bin/env python3
"""Telegram bridge for Mounir — talk to the assistant from your phone.

    python telegram_cli.py

Long polling (pyTelegramBotAPI): the laptop keeps asking Telegram's servers
for new messages, so every connection is OUTBOUND — no public IP, no port
forwarding, nothing exposed. Messages sent while the bridge is down wait in
Telegram's cloud and are delivered when it comes back.

Setup (one time):
 1. In Telegram, talk to @BotFather → /newbot → copy the token.
 2. export TELEGRAM_BOT_TOKEN="123:abc"      (e.g. in ~/.bashrc)
 3. Run this file, message your bot once — it replies with your chat id.
 4. export TELEGRAM_CHAT_ID="<that id>" and restart. From then on ONLY that
    chat is answered; anyone else who finds the bot is refused.

In-chat commands: /reset forgets the conversation, /start says hello.
Tool confirmations (e.g. sending an email) arrive as a question in the chat —
reply "yes" to approve, anything else cancels.
"""

from __future__ import annotations

import threading
import time

import telebot

from mounir import config, llm, tools, trace
from mounir.agent import Agent

# Telegram hard limit per message; longer replies are split at line breaks.
MAX_MESSAGE_CHARS = 4096
# How long a tool confirmation waits for a "yes" before giving up.
CONFIRM_TIMEOUT_S = 120

# TeleBot validates the token format at construction, so use a well-formed
# placeholder when unset — main() refuses to start polling without a real one.
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN or "0:unset")
agent = Agent()

# One turn at a time: a second message queues until the current one finishes.
_turn_lock = threading.Lock()
# In-flight tool confirmation, if any: the handler routes the next incoming
# message here (as the yes/no answer) instead of starting a new turn.
_confirm: dict = {"event": None, "answer": False}


def _allowed_chat() -> int | None:
    return int(config.TELEGRAM_CHAT_ID) if config.TELEGRAM_CHAT_ID.strip() else None


def _split(text: str) -> list[str]:
    """Split a long reply into Telegram-sized chunks, preferring line breaks."""
    chunks: list[str] = []
    while len(text) > MAX_MESSAGE_CHARS:
        cut = text.rfind("\n", 0, MAX_MESSAGE_CHARS)
        if cut < MAX_MESSAGE_CHARS // 2:  # no decent break point — hard cut
            cut = MAX_MESSAGE_CHARS
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def _send(chat_id: int, text: str) -> None:
    """Send a reply, styled as Markdown when Telegram accepts it.

    Telegram's Markdown parser rejects unbalanced */_/` entities, which LLM
    output produces now and then — fall back to plain text instead of failing.
    """
    for chunk in _split(text):
        try:
            bot.send_message(chat_id, chunk, parse_mode="Markdown")
        except telebot.apihelper.ApiTelegramException:
            bot.send_message(chat_id, chunk)


def _telegram_confirm(action: str) -> bool:
    """Ask for a tool confirmation in the chat; the next message is the answer.

    Runs on the turn's worker thread while it holds _turn_lock; the answer
    arrives on another polling thread via the main handler.
    """
    chat_id = _allowed_chat()
    if chat_id is None:
        return False
    event = threading.Event()
    _confirm["event"], _confirm["answer"] = event, False
    _send(chat_id, f"⚠ Needs your OK:\n{action}\n\nReply \"yes\" to approve.")
    answered = event.wait(CONFIRM_TIMEOUT_S)
    _confirm["event"] = None
    if not answered:
        _send(chat_id, "No answer — cancelled.")
        return False
    return _confirm["answer"]


def _keep_typing(chat_id: int, done: threading.Event) -> None:
    """Show 'typing…' in the chat while the agent works (it expires every 5s)."""
    while not done.is_set():
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        done.wait(4)


@bot.message_handler(content_types=["text"])
def _handle(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    allowed = _allowed_chat()

    # First-run discovery: no allowed chat configured yet — hand out the id.
    if allowed is None:
        bot.reply_to(
            message,
            f"This chat's id is {chat_id}.\n"
            f'Set TELEGRAM_CHAT_ID="{chat_id}" and restart the bridge to pair it.',
        )
        return
    if chat_id != allowed:
        bot.reply_to(message, "Sorry, this is a private assistant.")
        return

    text = (message.text or "").strip()
    if not text:
        return

    # A pending tool confirmation eats the next message as its yes/no answer.
    pending = _confirm["event"]
    if pending is not None:
        _confirm["answer"] = text.lower() in ("y", "yes")
        pending.set()
        return

    if text == "/start":
        _send(chat_id, "Mounir here. Say the word.")
        return
    if text == "/reset":
        agent.conversation.reset()
        _send(chat_id, "Conversation cleared.")
        return

    with _turn_lock:
        trace.node("telegram")
        trace.event(f"← {text[:120]}")
        done = threading.Event()
        typing = threading.Thread(target=_keep_typing, args=(chat_id, done), daemon=True)
        typing.start()
        try:
            reply = "".join(agent.respond(text)).strip()
        except Exception as exc:
            reply = f"[error] {exc}"
        finally:
            done.set()
            typing.join(timeout=1)
        _send(chat_id, reply or "(no reply)")
        trace.event(f"→ {len(reply)} chars")


@bot.message_handler(
    content_types=["photo", "voice", "audio", "video", "document", "sticker", "location"]
)
def _handle_other(message: telebot.types.Message) -> None:
    if _allowed_chat() == message.chat.id:
        bot.reply_to(message, "I can only read text here for now.")


def main() -> int:
    if not config.TELEGRAM_BOT_TOKEN:
        print('TELEGRAM_BOT_TOKEN is not set. Get one from @BotFather, then\n'
              'export TELEGRAM_BOT_TOKEN="123:abc" and run again.')
        return 1
    if not llm.is_up():
        print("Can't reach Ollama. Start it with `ollama serve` first.")
        return 1

    tools.confirm_fn = _telegram_confirm  # approvals happen in the chat

    me = bot.get_me()
    trace.banner("phone in your pocket, brain on your desk.")
    trace.rule(64)
    trace.agent_row("Agent", llm.active_model(agent.model))
    trace.kv("bridge", f"@{me.username} (long polling)")
    paired = _allowed_chat()
    trace.kv("paired chat", str(paired) if paired else "NONE — message the bot to get your chat id")
    trace.rule(64)
    print("  Ctrl+C to stop.\n")

    # Auto-reconnects on network errors; polling only fetches what we handle.
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
