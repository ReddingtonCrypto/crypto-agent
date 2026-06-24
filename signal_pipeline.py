import sqlite3
import asyncio
from telegram import Bot

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID



def save_signal(
    coin,
    direction,
    entry,
    stop,
    tp1,
    tp2,
    score,
    timeframe=None
):

    conn = sqlite3.connect(
        "database/crypto.db"
    )

    cursor = conn.cursor()


    cursor.execute(
    """
    INSERT INTO signals
    (
    coin,
    direction,
    entry,
    stop,
    tp1,
    tp2,
    score,
    timeframe
    )
    VALUES (?,?,?,?,?,?,?,?)
    """,
    (
        coin,
        direction,
        entry,
        stop,
        tp1,
        tp2,
        score,
        timeframe
    ))


    conn.commit()
    conn.close()


    print("Signal saved:", coin, timeframe or "")



async def send_alert(message):

    bot = Bot(token=TELEGRAM_TOKEN)

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message
    )


def _last_signature():
    """The 'signature' string of the most recent alert (we store the top-3
    set in the coin column). Returns None if we've never alerted."""
    conn = sqlite3.connect("database/crypto.db")
    row = conn.execute(
        "SELECT coin FROM alerts ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0] if row else None


def is_new_alert(signature):
    """True when the top-3 set has changed since the last ping (so we alert on
    anything new without spamming the same set every scan)."""
    return _last_signature() != signature


def record_alert(signature):
    conn = sqlite3.connect("database/crypto.db")
    conn.execute(
        "INSERT INTO alerts (coin, direction, score) VALUES (?, '', 0)",
        (signature,),
    )
    conn.commit()
    conn.close()


