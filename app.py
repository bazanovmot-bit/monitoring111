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
MAX_BUDGET_RUB = int(os.getenv("MAX_BUDGET_RUB", "3000"))

# Russian requests for website development. Intentionally accepts varied wording.
INTENT = re.compile(
    r"(нужен|нужна|нужно|ищу|посоветуйте|подскажите|кто может|кто-нибудь может|"
    r"кто сделает|кто разработает|заказать|требуется|хочу сделать|планирую|"
    r"ищем исполнителя|нужен исполнитель|ищу специалиста).{0,140}"
    r"(сайт|лендинг|сайт-визитк|интернет-магазин|веб-разработчик|разработчик сайта|"
    r"создание сайта|разработка сайта)|"
    r"(сайт|лендинг|сайт-визитк|интернет-магазин).{0,120}"
    r"(под ключ|для бизнеса|с нуля|разработать|создать|сделать|заказать|нужен|нужна)",
    re.IGNORECASE | re.DOTALL,
)
NOISE = re.compile(
    r"(ищем сотрудников|вакансия|резюме|портфолио разработчика|"
    r"предлагаю услуги|оказываю услуги|наши услуги|скидка|акция|"
    r"продам курс|обучение разработке|ищу работу|ищу подработку)",
    re.IGNORECASE,
)

# Amounts are considered budget figures only when accompanied by a currency marker
# or located near words that indicate payment/budget.
AMOUNT_RE = re.compile(
    r"(?<!\w)(\d{1,3}(?:[ .]\d{3})+|\d{1,6})(?:\s*(тысяч(?:а|и)?|т\.р\.|к))?"
    r"\s*(₽|руб(?:лей|ля|ль)?|р\.?)?",
    re.IGNORECASE,
)
BUDGET_CONTEXT = re.compile(
    r"(бюджет|оплат[аы]|стоимост[ьи]|цен[ауы]|заплатить|за работу|"
    r"за сайт|за лендинг|готов[аы] заплатить|вознаграждение|гонорар)",
    re.IGNORECASE,
)
WORD_AMOUNTS = {
    "тысяча": 1000, "тысячи": 1000, "тысяч": 1000,
    "две тысячи": 2000, "двух тысяч": 2000,
    "три тысячи": 3000, "трех тысяч": 3000, "трёх тысяч": 3000,
    "четыре тысячи": 4000, "четырех тысяч": 4000, "четырёх тысяч": 4000,
    "пять тысяч": 5000, "шесть тысяч": 6000, "семь тысяч": 7000,
    "десять тысяч": 10000,
}

def extract_budget_amounts(message: str):
    amounts = []
    for match in AMOUNT_RE.finditer(message):
        raw_number, multiplier, currency = match.groups()
        before = message[max(0, match.start() - 55):match.start()]
        after = message[match.end():min(len(message), match.end() + 25)]
        if not (currency or multiplier or BUDGET_CONTEXT.search(before) or BUDGET_CONTEXT.search(after)):
            continue
        number = int(re.sub(r"[ .]", "", raw_number))
        if multiplier:
            number *= 1000
        amounts.append(number)

    lower = message.lower()
    for phrase, value in WORD_AMOUNTS.items():
        if phrase in lower and BUDGET_CONTEXT.search(lower):
            amounts.append(value)
    return amounts

def budget_is_acceptable(message: str) -> bool:
    amounts = extract_budget_amounts(message)
    # No stated budget: keep the lead rather than miss a potential client.
    # For ranges, checking the maximum value avoids sending budgets above the cap.
    return not amounts or max(amounts) <= MAX_BUDGET_RUB

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
    log.info("Monitoring configured sources: %s; max budget: %s RUB",
             ", ".join("@" + s for s in sources), MAX_BUDGET_RUB)

    @client.on(events.NewMessage(chats=sources))
    async def on_message(event):
        try:
            message = event.raw_text or ""
            if len(message.strip()) < 15 or not INTENT.search(message) or NOISE.search(message):
                return
            if not budget_is_acceptable(message):
                log.info("Lead skipped: stated budget exceeds %s RUB", MAX_BUDGET_RUB)
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
            amounts = extract_budget_amounts(message)
            budget_label = f"до {MAX_BUDGET_RUB} ₽" if not amounts else f"указано: {max(amounts)} ₽"
            alert = (
                f"🔎 Возможная заявка на разработку сайта\n"
                f"Бюджет: {budget_label}\n"
                f"Источник: {chat_title}"
            )
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
