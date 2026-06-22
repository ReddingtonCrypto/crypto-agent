import ccxt
import pandas as pd
import asyncio
from telegram import Bot

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


exchange = ccxt.okx()


coins = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "ADA/USDT"
]


signals = []


for coin in coins:

    bars = exchange.fetch_ohlcv(
        coin,
        timeframe="1h",
        limit=100
    )


    df = pd.DataFrame(
        bars,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )


    # EMA

    df["EMA20"] = df.close.ewm(span=20).mean()
    df["EMA50"] = df.close.ewm(span=50).mean()


    # ATR

    high_low = df.high - df.low

    high_close = abs(
        df.high - df.close.shift()
    )

    low_close = abs(
        df.low - df.close.shift()
    )


    tr = pd.concat(
        [high_low, high_close, low_close],
        axis=1
    ).max(axis=1)


    df["ATR"] = tr.rolling(14).mean()



    # RSI

    delta = df.close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    rs = (
        gain.rolling(14).mean()
        /
        loss.rolling(14).mean()
    )

    df["RSI"] = 100 - (100/(1+rs))



    latest = df.iloc[-1]


    if latest.EMA20 > latest.EMA50:
        direction = "LONG"
    else:
        direction = "SHORT"


    score = 50


    if direction == "LONG":
        score += 20

    else:
        score += 20


    if latest.RSI > 55:
        score += 15

    elif latest.RSI < 45:
        score += 15



    signals.append(
        {
            "coin": coin,
            "direction": direction,
            "score": score,
            "price": latest.close,
            "atr": latest.ATR,
            "rsi": latest.RSI
        }
    )



best = max(
    signals,
    key=lambda x:x["score"]
)



price = best["price"]
atr = best["atr"]



if best["direction"] == "LONG":

    stop = price - atr*2
    tp1 = price + atr*3
    tp2 = price + atr*5

else:

    stop = price + atr*2
    tp1 = price - atr*3
    tp2 = price - atr*5



message = f"""
🚀 Crypto Agent Alert

Coin:
{best['coin']}

Direction:
{best['direction']}

Confidence:
{best['score']}%

Price:
{round(price,4)}

Entry:
{round(price,4)}

Stop:
{round(stop,4)}

TP1:
{round(tp1,4)}

TP2:
{round(tp2,4)}

RSI:
{round(best['rsi'],2)}
"""


async def send():

    bot = Bot(token=TELEGRAM_TOKEN)

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message
    )


asyncio.run(send())


print(message)