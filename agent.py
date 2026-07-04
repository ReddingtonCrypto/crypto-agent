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
from strategies.smc.ict_model import detect_ict, detect_mss
from strategies.smc.orderflow import cvd_proxy, cisd, volume_rising
from strategies.smc.volume_profile import value_area

exchange = ccxt.binanceus({
    "enableRateLimit": True,   # space out requests so the exchange doesn't temp-ban us
    "timeout": 30000,          # 30s per request before giving up
})


# Trade horizons -> the candle timeframe(s) each one scans.
# Day Trade = minutes/hours, Swing = a few days, Long-term = weeks/months.
TIMEFRAMES = [
    ("Day Trade", "1h"),
    ("Swing", "4h"),
]
# (15m removed 2026-06-30 — backtest proved it dragged Trend negative.)

# Which timeframe must AGREE on direction before a Trend signal is allowed.
# 1h confirms UP the ladder (don't fight the bigger trend);
# 4h confirms DOWN the ladder (a reversal shows on the lower TF first).
CONFIRM_TF = {
    "1h": "4h",
    "4h": "1h",
}

# Risk caps so a one-sided market can't pile up dozens of correlated trades.
MAX_OPEN_TRADES = 20            # total positions open at once
MAX_OPEN_PER_DIRECTION = 14     # of those, how many may be the same side

ENABLE_TREND = False            # Trend strategy off (backtest: net loser); ICT-focused
ENABLE_MSS = False              # standalone MSS off (backtest: ~break-even +0.04%); ICT (sweep+MSS+FVG) is the edge
UNIVERSE_SIZE = 20              # top N by market cap only — alts crush the edge (backtest: majors +0.76 vs +alts +0.02)

# Volume Profile location filter (backtest: +0.36 -> ~+0.50-0.75/trade on majors,
# +1.54 -> +2.34 on the live universe; robust across 30/50/70 bins). Only take a
# LONG that enters at/below the volume POC (discount) or a SHORT at/above it
# (premium) — don't chase into where volume was already done. Cuts trade count
# ~2/3, so signals get rarer. Toggle here; backtester reads the same flags.
ENABLE_VP = True
VP_BINS = 50


def _btc_dir(timeframe):
    bars = fetch_candles("BTC/USDT", timeframe)
    df = pd.DataFrame(
        bars, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    add_indicators(df)
    latest = df.iloc[:-1].iloc[-1]
    return "LONG" if latest.EMA20 > latest.EMA50 else "SHORT"


def market_bias():
    """Broad-market direction from BTC, combining the DAILY and 4H trend so it
    stays responsive: if both agree -> that bias; if they disagree (market
    turning) -> 'BOTH' (no strong bias). Used as a SOFT confidence tilt, not a
    hard gate."""
    try:
        daily = _btc_dir("1d")
        h4 = _btc_dir("4h")
        return daily if daily == h4 else "BOTH"
    except Exception as e:
        print(f"Market bias unavailable ({type(e).__name__}); allowing both sides.")
        return "BOTH"


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


def add_indicators(df):
    """Add EMA/RSI/ATR/volume columns to a candle DataFrame (in place)."""
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
    df["VOL_SMA"] = df.volume.rolling(20).mean()
    return df


def analyze_tf(coin, timeframe, horizon):
    """Fetch one coin/timeframe and analyse the last CLOSED candle."""
    candles = fetch_candles(coin, timeframe)

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    add_indicators(df)

    # Decide on the last CLOSED candle (drop the still-forming one) -> no repaint.
    closed = df.iloc[:-1]
    if len(closed) < 55:
        return None
    return evaluate(closed, coin, timeframe, horizon)


def evaluate(closed, coin, timeframe, horizon):
    """Run all strategies on a closed-candle DataFrame (must already have
    indicator columns). Returns trend_dir + price + a `signals` list. Shared
    by the live bot and the backtester."""
    latest = closed.iloc[-1]

    trend_dir = "LONG" if latest.EMA20 > latest.EMA50 else "SHORT"

    result = {
        "coin": coin,
        "timeframe": timeframe,
        "horizon": horizon,
        "trend_dir": trend_dir,
        "price": float(latest.close),
        "high": float(latest.high),
        "low": float(latest.low),
        "signals": [],
    }

    vol_sma = latest.VOL_SMA
    vol_confirm = bool(pd.notna(vol_sma) and latest.volume > vol_sma)
    regime = get_regime(latest.EMA20, latest.EMA50, latest.RSI)
    quality = market_quality(
        latest.volume, closed.volume.mean(), latest.ATR, latest.close
    )

    # SMC context — computed once, reused by every signal on this timeframe.
    smc = detect_structure(closed, lookback=2)
    smc_tag = f"{smc['event']} {smc['direction']}" if smc["event"] else "-"
    feats = smc_analyze(closed)

    # Volume Profile POC — the price where most volume traded, used as a
    # location filter (enter at discount/premium, not into the POC magnet).
    vp = value_area(closed, bins=VP_BINS) if ENABLE_VP else None
    vp_poc = vp["poc"] if vp else None

    # Order-flow reads (CVD proxy + CISD) for direction accuracy.
    cvd = cvd_proxy(closed)
    cisd_sig = cisd(closed)
    vol_rising = volume_rising(closed)
    flow_tags = []
    if cvd != "NEUTRAL":
        flow_tags.append(f"CVD {cvd}")
    if cisd_sig:
        flow_tags.append(f"CISD {cisd_sig}")

    def make(strategy, direction, base_conf, stop_level=None):
        conf = base_conf
        if vol_confirm:
            conf = min(100, conf + 5)
        # SMC structure: BOS = continuation (boost), CHoCH = reversal warning.
        if smc["event"] and strategy == "Trend":
            aligned = (
                (direction == "LONG" and smc["direction"] == "BULLISH")
                or (direction == "SHORT" and smc["direction"] == "BEARISH")
            )
            if smc["event"] == "BOS" and aligned:
                conf = min(100, conf + 10)
            elif smc["event"] == "CHoCH" and not aligned:
                conf = max(0, conf - 10)
        # SMC features bias.
        if feats["bias"] == "BULLISH" and direction == "LONG":
            conf = min(100, conf + min(10, feats["bull"] * 2))
        elif feats["bias"] == "BEARISH" and direction == "SHORT":
            conf = min(100, conf + min(10, feats["bear"] * 2))
        elif feats["bias"] == "BULLISH" and direction == "SHORT":
            conf = max(0, conf - 5)
        elif feats["bias"] == "BEARISH" and direction == "LONG":
            conf = max(0, conf - 5)
        # CVD (order-flow) agreement.
        if cvd == ("BULLISH" if direction == "LONG" else "BEARISH"):
            conf = min(100, conf + 5)
        elif cvd != "NEUTRAL":
            conf = max(0, conf - 5)
        # CISD (state-of-delivery flip) agreement.
        if cisd_sig == ("BULLISH" if direction == "LONG" else "BEARISH"):
            conf = min(100, conf + 5)
        elif cisd_sig:
            conf = max(0, conf - 5)
        # Real participation: rising volume backs the move.
        if vol_rising:
            conf = min(100, conf + 3)
        return {
            "coin": coin,
            "timeframe": timeframe,
            "horizon": horizon,
            "strategy": strategy,
            "direction": direction,
            "confidence": conf,
            "price": float(latest.close),
            "rsi": float(latest.RSI),
            "regime": regime,
            "quality": quality,
            "atr": float(latest.ATR),
            "smc": smc_tag,
            "smc_features": feats["tags"] + flow_tags,
            "vol_confirm": vol_confirm,
            "stop_level": stop_level,
            "cvd": cvd,
            "vp_poc": vp_poc,
        }

    # ----- Trend strategy: DISABLED 2026-06-30 (backtest: net loser). The EMA
    #  trend_dir is still used for multi-timeframe confirmation above. -----
    if ENABLE_TREND and regime in ("TREND_BULL", "TREND_BEAR"):
        result["signals"].append(make(
            "Trend", trend_dir,
            calculate_confidence(latest.EMA20, latest.EMA50, latest.close, latest.RSI),
        ))

    # ----- ICT model (sweep -> MSS -> FVG) -----
    ict = detect_ict(closed)
    if ict:
        result["signals"].append(
            make("ICT", ict["direction"], 85, stop_level=ict["swept"])
        )

    # ----- MSS strategy (sweep -> MSS, no FVG) — DISABLED: backtest ~break-even.
    #  The FVG confluence (in ICT) is what makes the edge. -----
    if ENABLE_MSS:
        mss = detect_mss(closed)
        if mss:
            result["signals"].append(
                make("MSS", mss["direction"], 80, stop_level=mss["swept"])
            )

    return result


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
        # Backtest showed tightening Trend's entry (confidence bar / CVD gate)
        # only HURT expectancy — so we leave its rules alone. Trend's live
        # problem was the forced shorts, which the soft market filter fixes.
        if s["direction"] == "LONG" and s["rsi"] > 75:
            return False
        if s["direction"] == "SHORT" and s["rsi"] < 25:
            return False
        return True

    if s["strategy"] in ("ICT", "MSS"):
        # Volume Profile location filter: don't chase into the POC — LONG must
        # enter at/below it (discount), SHORT at/above it (premium).
        if ENABLE_VP and s.get("vp_poc") is not None:
            if s["direction"] == "LONG" and s["price"] > s["vp_poc"]:
                return False
            if s["direction"] == "SHORT" and s["price"] < s["vp_poc"]:
                return False
        # The structure sequence is the entry logic; the common checks above
        # (confidence/quality/volume) are enough.
        return True

    return False


def run_agent():

    print("\n==============================")
    print("Scanning market...")
    print(datetime.now())
    print("==============================\n")

    coins = universe.get_universe(exchange, limit=100)[:UNIVERSE_SIZE]
    bias = market_bias()
    print(
        f"Watching {len(coins)} coins (top {UNIVERSE_SIZE} by mcap) across "
        f"{len(TIMEFRAMES)} timeframes on {exchange.id}\n"
        f"Market bias (BTC daily): {bias}\n"
    )

    signals = []
    bar_map = {}
    for coin in coins:
        # Analyse every timeframe for this coin first (so we have the
        # confirmation timeframe's direction on hand).
        per_tf = {}
        for horizon, tf in TIMEFRAMES:
            try:
                r = analyze_tf(coin, tf, horizon)
                if r is not None:
                    per_tf[tf] = r
                    # Track trades against the FRESHEST candle (smallest TF,
                    # which is listed first), using its high/low for intraday hits.
                    bar_map.setdefault(coin, {
                        "high": r["high"], "low": r["low"], "price": r["price"],
                    })
            except Exception as e:
                print(f"Error scanning {coin} {tf}: {type(e).__name__}: {e}")

        # Now collect signals, applying multi-timeframe confirmation.
        for horizon, tf in TIMEFRAMES:
            r = per_tf.get(tf)
            if not r:
                continue
            for sig in r.get("signals", []):
                if sig["strategy"] == "Trend":
                    ctf = CONFIRM_TF.get(tf)
                    cr = per_tf.get(ctf)
                    if cr is None or cr["trend_dir"] != sig["direction"]:
                        continue  # confirmation timeframe disagrees -> skip
                    sig["confirm"] = f"{ctf} agrees"
                signals.append(sig)

    if not bar_map:
        print("No data collected")
        return

    # 1) Advance paper trades (partial-exit plan); ping Telegram for each event
    #    — a partial bank at TP1, or a full close (WIN/LOSS/EXPIRED).
    for t in paper_trading.update_open_trades(bar_map):
        mark = t["result"]
        emoji = {"WIN": "✅", "LOSS": "❌", "EXPIRED": "⌛", "TP1": "🎯"}.get(mark, "❌")
        tf = t.get("timeframe", "")
        if mark == "TP1":
            print(f"TP1 hit (half banked): {t['coin']} {t['direction']} {tf} +{t['pnl_pct']}%")
            asyncio.run(send_alert(
                f"{emoji} TP1 HIT — half banked  {t['coin']}  {t['direction']}  ({tf})\n"
                f"Locked: +{t['pnl_pct']}%  ·  runner to TP2, stop at break-even"
            ))
        else:
            print(f"Trade closed: {t['coin']} {t['direction']} {tf} {mark} {t['pnl_pct']}%")
            asyncio.run(send_alert(
                f"{emoji} {mark}  {t['coin']}  {t['direction']}  ({tf})\n"
                f"Result: {t['pnl_pct']}%"
            ))

    # 2) Soft market-bias tilt: favour BTC's direction, but don't hard-block —
    #    a strong enough counter-setup can still pass. (Caps handle concentration.)
    if bias != "BOTH":
        for s in signals:
            if s["direction"] == bias:
                s["confidence"] = min(100, s["confidence"] + 5)
            else:
                s["confidence"] = max(0, s["confidence"] - 15)

    qualified = sorted(
        [s for s in signals if passes_filters(s)],
        key=lambda x: x["confidence"],
        reverse=True,
    )

    # 3) Log each new setup and open a paper trade for it (one per
    #    coin + side + timeframe), respecting the risk caps.
    open_count = paper_trading.get_stats()["open"]
    open_by_dir = paper_trading.open_counts_by_direction()
    for s in qualified:
        if open_count >= MAX_OPEN_TRADES:
            break
        if open_by_dir.get(s["direction"], 0) >= MAX_OPEN_PER_DIRECTION:
            continue

        trade = calculate_trade(
            s["price"], s["direction"], s["atr"], s["strategy"], s.get("stop_level")
        )
        opened = paper_trading.open_trade(
            s["coin"], s["direction"],
            trade["entry"], trade["stop"], trade["tp1"], trade["tp2"],
            s["confidence"], s["timeframe"], s["strategy"],
        )
        if opened:
            save_signal(
                s["coin"], s["direction"],
                trade["entry"], trade["stop"], trade["tp1"], trade["tp2"],
                s["confidence"], s["timeframe"], s["strategy"],
            )
            open_count += 1
            open_by_dir[s["direction"]] = open_by_dir.get(s["direction"], 0) + 1

    # 4) Show the running accuracy scoreboard.
    stats = paper_trading.get_stats()
    print(
        f"\n=== PAPER TRADING SCOREBOARD ===\n"
        f"Open: {stats['open']} | Closed: {stats['closed']} | "
        f"Wins: {stats['wins']} | Losses: {stats['losses']} | "
        f"Expired: {stats['expired']} | "
        f"Win rate: {stats['win_rate']}% | Avg P&L: {stats['avg_pnl']}%\n"
    )

    if not qualified:
        print("No valid signals found")
        return

    best = qualified[0]
    trade = calculate_trade(
        best["price"], best["direction"], best["atr"], best["strategy"], best.get("stop_level")
    )

    print(
        f"""===== BEST SIGNAL =====
Coin: {best['coin']}
Horizon: {best['horizon']} ({best['timeframe']})
Strategy: {best['strategy']}
Direction: {best['direction']}
Confidence: {best['confidence']}%
Confirmation: {best.get('confirm', 'n/a (range)')}
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
            tr = calculate_trade(
                s["price"], s["direction"], s["atr"], s["strategy"], s.get("stop_level")
            )
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
