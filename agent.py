import asyncio
import time
from datetime import datetime

import ccxt
import pandas as pd

import universe
import paper_trading
from formatting import fmt_price
from risk_engine import calculate_trade
from signal_pipeline import save_signal, send_alert, is_new_alert, record_alert
from confidence_engine import calculate_confidence
from regime_engine import get_regime
from market_filter import market_quality

exchange = ccxt.binanceus({
    "enableRateLimit": True,   # space out requests so the exchange doesn't temp-ban us
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


def scan_one(coin):
    """Fetch one coin, compute indicators, and return its signal dict
    (or None if there isn't enough data)."""
    candles = fetch_candles(coin)

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
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

    df["ATR"] = (df.high - df.low).rolling(14).mean()

    latest = df.iloc[-1]

    direction = "LONG" if latest.EMA20 > latest.EMA50 else "SHORT"

    confidence = calculate_confidence(
        latest.EMA20, latest.EMA50, latest.close, latest.RSI
    )
    regime = get_regime(latest.EMA20, latest.EMA50, latest.RSI)
    quality = market_quality(
        latest.volume, df.volume.mean(), latest.ATR, latest.close
    )

    return {
        "coin": coin,
        "direction": direction,
        "confidence": confidence,
        "price": float(latest.close),
        "rsi": float(latest.RSI),
        "regime": regime,
        "quality": quality,
        "atr": float(latest.ATR),
    }


def passes_filters(s):
    """The rules that decide whether a coin is a tradeable signal."""
    if s["confidence"] < 70:
        return False
    if s["quality"] != "STRONG":
        return False
    if s["regime"] in ["RANGE", "WEAK_TREND"]:
        return False
    if s["direction"] == "LONG" and s["rsi"] > 75:
        return False
    if s["direction"] == "SHORT" and s["rsi"] < 25:
        return False
    return True


def run_agent():

    print("\n==============================")
    print("Scanning market...")
    print(datetime.now())
    print("==============================\n")

    coins = universe.get_universe(exchange, limit=100)
    print(f"Watching {len(coins)} coins (top by market cap, tradeable on {exchange.id})\n")

    signals = []
    for coin in coins:
        try:
            s = scan_one(coin)
            if s is not None:
                signals.append(s)
        except Exception as e:
            print(f"Error scanning {coin}: {type(e).__name__}: {e}")

    if not signals:
        print("No signals collected")
        return

    # 1) Update existing paper trades; ping Telegram for any that closed.
    price_map = {s["coin"]: s["price"] for s in signals}
    closed = paper_trading.update_open_trades(price_map)
    for t in closed:
        mark = "WIN" if t["result"] == "WIN" else "LOSS"
        emoji = "✅" if t["result"] == "WIN" else "❌"
        print(f"Trade closed: {t['coin']} {t['direction']} {mark} {t['pnl_pct']}%")
        asyncio.run(send_alert(
            f"{emoji} {mark}  {t['coin']}  {t['direction']}\n"
            f"Result: {t['pnl_pct']}%"
        ))

    # 2) Find every qualifying signal, best first.
    qualified = sorted(
        [s for s in signals if passes_filters(s)],
        key=lambda x: x["confidence"],
        reverse=True,
    )

    # 3) Log each new setup and open a paper trade for it (one per coin+side).
    for s in qualified:
        trade = calculate_trade(s["price"], s["direction"], s["atr"])
        opened = paper_trading.open_trade(
            s["coin"], s["direction"],
            trade["entry"], trade["stop"], trade["tp1"], trade["tp2"],
            s["confidence"],
        )
        if opened:
            save_signal(
                s["coin"], s["direction"],
                trade["entry"], trade["stop"], trade["tp1"], trade["tp2"],
                s["confidence"],
            )

    # 4) Show the running accuracy scoreboard.
    stats = paper_trading.get_stats()
    print(
        f"\n=== PAPER TRADING SCOREBOARD ===\n"
        f"Open: {stats['open']} | Closed: {stats['closed']} | "
        f"Wins: {stats['wins']} | Losses: {stats['losses']} | "
        f"Win rate: {stats['win_rate']}% | Avg P&L: {stats['avg_pnl']}%\n"
    )

    if not qualified:
        print("No valid signals found")
        return

    best = qualified[0]
    trade = calculate_trade(best["price"], best["direction"], best["atr"])

    print(
        f"""===== BEST SIGNAL =====
Coin: {best['coin']}
Direction: {best['direction']}
Confidence: {best['confidence']}%
Regime: {best['regime']}
Quality: {best['quality']}
RSI: {round(best['rsi'], 2)}
Price: {best['price']}
"""
    )

    # 5) Ping Telegram with the TOP 3 signals, but only when the top-3 SET
    #    changes (so you see anything new without repeat spam).
    top3 = qualified[:3]
    signature = "|".join(sorted(f"{s['coin']}:{s['direction']}" for s in top3))

    if is_new_alert(signature):
        lines = ["CRYPTO AGENT - TOP SIGNALS\n"]
        for i, s in enumerate(top3, 1):
            tr = calculate_trade(s["price"], s["direction"], s["atr"])
            lines.append(
                f"{i}) {s['coin']}  {s['direction']}  {s['confidence']}%\n"
                f"   Entry {fmt_price(tr['entry'])}\n"
                f"   Stop  {fmt_price(tr['stop'])}\n"
                f"   TP1   {fmt_price(tr['tp1'])}   TP2 {fmt_price(tr['tp2'])}\n"
            )
        message = "\n".join(lines)

        asyncio.run(send_alert(message))
        record_alert(signature)
        print(message)
    else:
        print("Top-3 unchanged since last alert - logged, no repeat ping")


# Loop forever only when run directly (python agent.py) on your own machine.
# On GitHub Actions we import run_agent() from scan_once.py instead, so this
# loop must NOT run there.
if __name__ == "__main__":

    while True:

        try:
            run_agent()

        except Exception as e:
            print("Agent error:", e)

        print("\nWaiting 15 minutes...\n")

        time.sleep(900)
