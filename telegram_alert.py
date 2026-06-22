from telegram import Bot
import asyncio

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


async def send_alert():

    bot = Bot(token=TELEGRAM_TOKEN)

    message = """
🚀 Crypto Agent Alert

Coin: BTC/USDT

Signal: SHORT

Confidence: 80%

Entry:
104000

Stop:
106000

TP1:
102000

TP2:
99000

Regime:
TREND_BEAR
"""

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message
    )


asyncio.run(send_alert())