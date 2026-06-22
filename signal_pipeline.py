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
    score
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
    score
    )
    VALUES (?,?,?,?,?,?,?)
    """,
    (
        coin,
        direction,
        entry,
        stop,
        tp1,
        tp2,
        score
    ))


    conn.commit()
    conn.close()


    print("Signal saved:", coin)



async def send_alert(message):

    bot = Bot(token=TELEGRAM_TOKEN)

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message
    )


def last_alert():
    """Return (coin, direction) of the most recent Telegram alert, or None."""
    conn = sqlite3.connect("database/crypto.db")
    row = conn.execute(
        "SELECT coin, direction FROM alerts ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row


def is_new_alert(coin, direction):
    """True if this differs from the last alert we sent (so we don't ping the
    same setup over and over). Everything is still logged regardless."""
    last = last_alert()
    return last is None or last[0] != coin or last[1] != direction


def record_alert(coin, direction, score):
    conn = sqlite3.connect("database/crypto.db")
    conn.execute(
        "INSERT INTO alerts (coin, direction, score) VALUES (?,?,?)",
        (coin, direction, score),
    )
    conn.commit()
    conn.close()


