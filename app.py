import asyncio
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

import requests
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lead-monitor")

app = Flask(__name__)
DB_PATH = os.getenv("DB_PATH", "seen.sqlite3")

# Russian-language intent phrases. This is a first-pass filter, not an AI guarantee.
INTENT = re.compile(
    r"(нужен|нужна|нужно|ищу|посоветуйте|подскажите|кто может|"
    r"заказать|требуется|хочу сделать|планирую)\\b.{0,100}\\b"
    r"(сайт|лендинг|сайт-визитк|интернет-магазин|веб-разработчик|разработчик сайта)|"
    r"(сайт|лендинг|сайт-визитк|интернет-магазин).{0,100}\\b"
    r"(под ключ|для бизнеса|с нуля|разработать|создать|сделать)",
    re.IGNORECASE | re.DOTALL,
)
NOISE = re.compile(
    r"(ищем сотрудников|вакансия|резюме|портфолио разработчика|"
    r"предлагаю услуги|оказываю услуги|наши услуги|скидка|акция|"
    r"продам курс|обучение разработке)",
    re.IGNORECASE,
)

def db_init():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("CREATE TABLE IF NOT EXISTS seen (message_key TEXT PRIMARY KEY, seen_at TEXT NOT NULL)")

def already_seen(key: str) -> bool:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT 1 FROM seen WHERE message_key = ?", (key,))
        if cur.fetchone():
            return True
        con.execute("INSERT INTO seen(message_key, seen_at) VALUES (?, ?)",
                    (key, datetime.now(timezone.utc).isoformat()))
        return False

def send_alert(text: str):
    token = os.environ["BOT_TOKEN"]
    chat_id = os.environ["CHAT_ID"]
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()

async def run_monitor():
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session = os.environ["TELEGRAM_SESSION_STRING"]
    sources = [s.strip().lstrip("@") for s in os.getenv("SOURCES", "").split(",") if s.strip()]
    if not sources:
        raise RuntimeError("Set SOURCES to comma-separated public channel/group usernames")

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.start()
    log.info("Monitoring configured sources: %s", ", ".join("@" + s for s in sources))

    @client.on(events.NewMessage(chats=sources))
    async def on_message(event):
        try:
            message = event.raw_text or ""
            if len(message.strip()) < 15 or not INTENT.search(message) or NOISE.search(message):
                return
            chat = await event.get_chat()
            username = getattr(chat, "username", None)
            chat_title = getattr(chat, "title", None) or "Telegram"
            msg_id = event.message.id
            key = f"{username or chat_title}:{msg_id}"
            if already_seen(key):
                return
            link = f"https://t.me/{username}/{msg_id}" if username else ""
            excerpt = message[:1800]
            alert = f"🔎 Возможная заявка на сайт\nИсточник: {chat_title}"
            if link:
                alert += f"\nСсылка: {link}"
            alert += f"\n\n{excerpt}"
            await asyncio.to_thread(send_alert, alert)
            log.info("Lead alert sent for %s", key)
        except Exception:
            log.exception("Failed to process message")

    await client.run_until_disconnected()

def monitor_thread():
    try:
        asyncio.run(run_monitor())
    except Exception:
        log.exception("Monitor stopped")

@app.get("/")
def health():
    return {"status": "ok", "service": "telegram-lead-monitor"}

if __name__ == "__main__":
    db_init()
    threading.Thread(target=monitor_thread, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
