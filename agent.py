import asyncio
import time
from datetime import datetime

import ccxt
import pandas as pd

from risk_engine import calculate_trade
from signal_pipeline import save_signal, send_alert
from signal_filter import can_send_signal
from confidence_engine import calculate_confidence
from regime_engine import get_regime
from market_filter import market_quality

exchange = ccxt.okx({
    "enableRateLimit": True,   # space out requests so OKX doesn't temp-ban us
    "timeout": 30000,          # 30s per request before giving up
})


def fetch_candles(coin, retries=3):
    """Fetch 1h candles with a few retries, so a single hiccup doesn't
    skip the coin for the whole scan."""
    for attempt in range(retries):
        try:
            return exchange.fetch_ohlcv(coin, timeframe="1h", limit=200)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3)

coins = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "BNB/USDT",
    "TRX/USDT",
    "LINK/USDT",
    "AVAX/USDT",
    "DOT/USDT",
    "ATOM/USDT",
    "LTC/USDT",
    "UNI/USDT",
    "AAVE/USDT",
    "SUI/USDT",
    "APT/USDT",
    "NEAR/USDT",
    "ARB/USDT",
    "OP/USDT"
]


def run_agent():

    signals = []

    print("\n==============================")
    print("Scanning market...")
    print(datetime.now())
    print("==============================\n")

    for coin in coins:

        try:

            candles = fetch_candles(coin)

            df = pd.DataFrame(
                candles,
                columns=[
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume"
                ]
            )

            df["EMA20"] = df.close.ewm(span=20).mean()
            df["EMA50"] = df.close.ewm(span=50).mean()

            delta = df.close.diff()

            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)

            avg_gain = gain.rolling(14).mean()
            avg_loss = loss.rolling(14).mean()

            rs = avg_gain / avg_loss

            df["RSI"] = 100 - (100 / (1 + rs))

            df["ATR"] = (
                df.high - df.low
            ).rolling(14).mean()

            latest = df.iloc[-1]

            if latest.EMA20 > latest.EMA50:
                direction = "LONG"
            else:
                direction = "SHORT"

            confidence = calculate_confidence(
                latest.EMA20,
                latest.EMA50,
                latest.close,
                latest.RSI
            )

            regime = get_regime(
                latest.EMA20,
                latest.EMA50,
                latest.RSI
            )

            avg_volume = df.volume.mean()

            quality = market_quality(
                latest.volume,
                avg_volume,
                latest.ATR,
                latest.close
            )

            signals.append({
                "coin": coin,
                "direction": direction,
                "confidence": confidence,
                "price": float(latest.close),
                "rsi": float(latest.RSI),
                "regime": regime,
                "quality": quality,
                "atr": float(latest.ATR)
            })

        except Exception as e:
            print(f"Error scanning {coin}: {type(e).__name__}: {e}")

    if not signals:
        print("No signals collected")
        return

    signals.sort(
        key=lambda x: x["confidence"],
        reverse=True
    )

    best = None

    for signal in signals:

        if signal["confidence"] < 70:
            continue

        if signal["quality"] != "STRONG":
            continue

        if signal["regime"] in ["RANGE", "WEAK_TREND"]:
            continue

        if signal["direction"] == "LONG" and signal["rsi"] > 75:
            continue

        if signal["direction"] == "SHORT" and signal["rsi"] < 25:
            continue

        best = signal
        break

    if best is None:
        print("No valid signals found")
        return

    print(
        f"""
===== BEST SIGNAL =====

Coin:
{best['coin']}

Direction:
{best['direction']}

Confidence:
{best['confidence']}%

Regime:
{best['regime']}

Quality:
{best['quality']}

RSI:
{round(best['rsi'], 2)}

Price:
{best['price']}
"""
    )

    trade = calculate_trade(
        best["price"],
        best["direction"],
        best["atr"]
    )

    if can_send_signal(
        best["coin"],
        best["direction"],
        best["price"],
        best["confidence"]
    ):

        save_signal(
            best["coin"],
            best["direction"],
            trade["entry"],
            trade["stop"],
            trade["tp1"],
            trade["tp2"],
            best["confidence"]
        )

        message = f"""

CRYPTO AGENT SIGNAL

Coin:
{best['coin']}

Direction:
{best['direction']}

Confidence:
{best['confidence']}%

Regime:
{best['regime']}

Quality:
{best['quality']}

RSI:
{round(best['rsi'], 2)}

Entry:
{trade['entry']}

Stop:
{trade['stop']}

TP1:
{trade['tp1']}

TP2:
{trade['tp2']}
"""

        asyncio.run(
            send_alert(message)
        )

        print(message)

    else:

        print(
            "Duplicate signal blocked"
        )


# Loop forever only when run directly (python agent.py) on your own machine.
# On GitHub Actions we import run_agent() from scan_once.py instead, so this
# loop must NOT run there.
if __name__ == "__main__":

    while True:

        try:
            run_agent()

        except Exception as e:
            print("Agent error:", e)

        print("\nWaiting 5 minutes...\n")

        time.sleep(300)
