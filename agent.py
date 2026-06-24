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
from strategies.smc.market_structure import detect_structure
from strategies.smc.smc_features import analyze as smc_analyze

exchange = ccxt.binanceus({
    "enableRateLimit": True,   # space out requests so the exchange doesn't temp-ban us
    "timeout": 30000,          # 30s per request before giving up
})


# Trade horizons -> the candle timeframe(s) each one scans.
# Day Trade = minutes/hours, Swing = a few days, Long-term = weeks/months.
TIMEFRAMES = [
    ("Day Trade", "15m"),
    ("Day Trade", "1h"),
    ("Swing", "4h"),
]


def fetch_candles(coin, timeframe, retries=3):
    """Fetch candles for one timeframe with a few retries, so a single
    hiccup doesn't skip the coin for the whole scan."""
    for attempt in range(retries):
        try:
            return exchange.fetch_ohlcv(coin, timeframe=timeframe, limit=200)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3)


def scan_one(coin, timeframe, horizon):
    """Fetch one coin on one timeframe, compute indicators, and return its
    signal dict (or None if there isn't enough data)."""
    candles = fetch_candles(coin, timeframe)

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

    # ----- Volume indicator: latest volume vs its 20-period average -----
    df["VOL_SMA"] = df.volume.rolling(20).mean()

    latest = df.iloc[-1]

    vol_sma = latest.VOL_SMA
    vol_confirm = bool(pd.notna(vol_sma) and latest.volume > vol_sma)

    regime = get_regime(latest.EMA20, latest.EMA50, latest.RSI)
    quality = market_quality(
        latest.volume, df.volume.mean(), latest.ATR, latest.close
    )

    # ----- Pick a strategy based on the regime -----
    if regime in ("TREND_BULL", "TREND_BEAR"):
        # TREND: ride the direction of the EMA stack.
        strategy = "Trend"
        direction = "LONG" if latest.EMA20 > latest.EMA50 else "SHORT"
        confidence = calculate_confidence(
            latest.EMA20, latest.EMA50, latest.close, latest.RSI
        )
    elif regime == "RANGE":
        # RANGE: fade the extremes (buy oversold support, sell overbought).
        strategy = "Range"
        if latest.RSI < 35:
            direction = "LONG"
        elif latest.RSI > 65:
            direction = "SHORT"
        else:
            return None  # sideways but no edge right now
        confidence = range_confidence(latest.RSI)
    else:
        return None  # WEAK_TREND / unclear -> no signal

    # Volume confirmation gives a small boost (and is required in passes_filters).
    if vol_confirm:
        confidence = min(100, confidence + 5)

    # ----- SMC: market structure (BOS / CHoCH) — trend continuation only -----
    smc = detect_structure(df, lookback=2)
    smc_tag = "-"
    if smc["event"]:
        smc_tag = f"{smc['event']} {smc['direction']}"  # e.g. "BOS BULLISH"
        if strategy == "Trend":
            aligned = (
                (direction == "LONG" and smc["direction"] == "BULLISH")
                or (direction == "SHORT" and smc["direction"] == "BEARISH")
            )
            if smc["event"] == "BOS" and aligned:
                confidence = min(100, confidence + 10)
            elif smc["event"] == "CHoCH" and not aligned:
                confidence = max(0, confidence - 10)

    # ----- SMC features: FVG, order/breaker/mitigation blocks, sweeps,
    #       equal highs/lows, stop hunts, session sweeps, MSS -----
    feats = smc_analyze(df)
    smc_features = feats["tags"]
    if feats["bias"] == "BULLISH" and direction == "LONG":
        confidence = min(100, confidence + min(10, feats["bull"] * 2))
    elif feats["bias"] == "BEARISH" and direction == "SHORT":
        confidence = min(100, confidence + min(10, feats["bear"] * 2))
    elif feats["bias"] == "BULLISH" and direction == "SHORT":
        confidence = max(0, confidence - 5)
    elif feats["bias"] == "BEARISH" and direction == "LONG":
        confidence = max(0, confidence - 5)

    return {
        "coin": coin,
        "strategy": strategy,
        "direction": direction,
        "confidence": confidence,
        "price": float(latest.close),
        "rsi": float(latest.RSI),
        "regime": regime,
        "quality": quality,
        "atr": float(latest.ATR),
        "smc": smc_tag,
        "smc_features": smc_features,
        "vol_confirm": vol_confirm,
        "horizon": horizon,
        "timeframe": timeframe,
    }


def range_confidence(rsi):
    """Confidence for a range (mean-reversion) signal: the more extreme the
    RSI, the stronger the edge."""
    c = 55
    if rsi < 25 or rsi > 75:
        c += 25
    elif rsi < 30 or rsi > 70:
        c += 15
    elif rsi < 35 or rsi > 65:
        c += 10
    return c


def passes_filters(s):
    """The rules that decide whether a coin is a tradeable signal."""
    # Common requirements for both strategies.
    if s["confidence"] < 70:
        return False
    if s["quality"] != "STRONG":
        return False
    if not s["vol_confirm"]:          # volume must confirm participation
        return False

    if s["strategy"] == "Trend":
        # Don't chase a trend that's already over-extended.
        if s["direction"] == "LONG" and s["rsi"] > 75:
            return False
        if s["direction"] == "SHORT" and s["rsi"] < 25:
            return False
        return True

    if s["strategy"] == "Range":
        # Range signals are only valid in an actual range.
        return s["regime"] == "RANGE"

    return False


def run_agent():

    print("\n==============================")
    print("Scanning market...")
    print(datetime.now())
    print("==============================\n")

    coins = universe.get_universe(exchange, limit=100)
    print(
        f"Watching {len(coins)} coins across {len(TIMEFRAMES)} timeframes "
        f"(tradeable on {exchange.id})\n"
    )

    signals = []
    for coin in coins:
        for horizon, tf in TIMEFRAMES:
            try:
                s = scan_one(coin, tf, horizon)
                if s is not None:
                    signals.append(s)
            except Exception as e:
                print(f"Error scanning {coin} {tf}: {type(e).__name__}: {e}")

    if not signals:
        print("No signals collected")
        return

    # 1) Update existing paper trades; ping Telegram for any that closed.
    price_map = {s["coin"]: s["price"] for s in signals}
    closed = paper_trading.update_open_trades(price_map)
    for t in closed:
        mark = "WIN" if t["result"] == "WIN" else "LOSS"
        emoji = "✅" if t["result"] == "WIN" else "❌"
        tf = t.get("timeframe", "")
        print(f"Trade closed: {t['coin']} {t['direction']} {tf} {mark} {t['pnl_pct']}%")
        asyncio.run(send_alert(
            f"{emoji} {mark}  {t['coin']}  {t['direction']}  ({tf})\n"
            f"Result: {t['pnl_pct']}%"
        ))

    # 2) Find every qualifying signal, best first.
    qualified = sorted(
        [s for s in signals if passes_filters(s)],
        key=lambda x: x["confidence"],
        reverse=True,
    )

    # 3) Log each new setup and open a paper trade for it
    #    (one per coin + side + timeframe).
    for s in qualified:
        trade = calculate_trade(s["price"], s["direction"], s["atr"])
        opened = paper_trading.open_trade(
            s["coin"], s["direction"],
            trade["entry"], trade["stop"], trade["tp1"], trade["tp2"],
            s["confidence"], s["timeframe"],
        )
        if opened:
            save_signal(
                s["coin"], s["direction"],
                trade["entry"], trade["stop"], trade["tp1"], trade["tp2"],
                s["confidence"], s["timeframe"],
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
Horizon: {best['horizon']} ({best['timeframe']})
Strategy: {best['strategy']}
Direction: {best['direction']}
Confidence: {best['confidence']}%
Regime: {best['regime']}
Quality: {best['quality']}
RSI: {round(best['rsi'], 2)}
Volume confirmed: {best['vol_confirm']}
Structure: {best.get('smc', '-')}
SMC: {', '.join(best.get('smc_features') or []) or '-'}
Price: {best['price']}
"""
    )

    # 5) Ping Telegram with the TOP 3 signals, but only when the top-3 SET
    #    changes (so you see anything new without repeat spam).
    top3 = qualified[:3]
    signature = "|".join(
        sorted(f"{s['coin']}:{s['direction']}:{s['timeframe']}" for s in top3)
    )

    if is_new_alert(signature):
        lines = ["CRYPTO AGENT - TOP SIGNALS\n"]
        for i, s in enumerate(top3, 1):
            tr = calculate_trade(s["price"], s["direction"], s["atr"])
            smc_line = f"   Structure {s['smc']}\n" if s.get("smc", "-") != "-" else ""
            feats = s.get("smc_features") or []
            feat_line = f"   SMC: {', '.join(feats[:3])}\n" if feats else ""
            lines.append(
                f"{i}) {s['coin']}  {s['direction']}  {s['confidence']}%\n"
                f"   {s['horizon']} ({s['timeframe']}) - {s['strategy']}\n"
                f"   Entry {fmt_price(tr['entry'])}\n"
                f"   Stop  {fmt_price(tr['stop'])}\n"
                f"   TP1   {fmt_price(tr['tp1'])}   TP2 {fmt_price(tr['tp2'])}\n"
                f"{smc_line}{feat_line}"
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
