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


