import asyncio
import time
from datetime import datetime

import ccxt
import pandas as pd

import universe
import paper_trading
import health_monitor
import sector_flow
from formatting import fmt_price
from risk_engine import calculate_trade
from signal_pipeline import save_signal, send_alert, is_new_alert, record_alert
from confidence_engine import calculate_confidence
from regime_engine import get_regime
from market_filter import market_quality
from strategies.smc.market_structure import detect_structure
from strategies.smc.smc_features import analyze as smc_analyze
from strategies.smc.ict_model import detect_ict, detect_mss
from strategies.smc.crt import detect_crt_aligned, detect_crt_setup, detect_crt_enhanced
from strategies.smc import ote as ote_lib
from strategies.smc.ote import detect_ote_live, htf_bias as ote_htf_bias

# Apply the group's "instant CISD" quality filter to the LIVE detector: the
# displacement/MSS must fire within 5 bars of the sweep origin (a sharp reversal,
# not a slow grind). Walk-forward-validated improvement on 1h (+0.09->+0.24%/tr)
# and 12h (+2.21->+3.02%/tr). Applies to both the strict track and the scanner.
ote_lib.INSTANT_CISD = 5
from strategies.smc.orderflow import cvd_proxy, cisd, volume_rising
from strategies.smc.volume_profile import value_area

from data_source import make_exchange

# binance.com (global) via the US-reachable vision data host — the venue we
# actually trade on — with automatic fallback to binanceus. See data_source.py.
exchange = make_exchange()


# Trade horizons -> the candle timeframe(s) each one scans.
# Day Trade = minutes/hours, Swing = a few days, Long-term = weeks/months.
# Each timeframe maps to a trade-TYPE (horizon) so alerts/dashboard show what
# kind of trade a signal is. Backtest note: 30m/1h/4h are net-negative, 12h/1d
# are POSITIVE (higher TFs = cleaner ICT structure) — kept all for signal
# coverage across trade styles; judge each TYPE on the dashboard breakdown.
TIMEFRAMES = [
    ("Scalp", "30m"),
    ("Day Trade", "1h"),
    ("Swing", "4h"),
    ("Position", "12h"),
    ("Long-term", "1d"),
    ("Weekly", "1w"),
]

# CRT (Candle Range Theory) — FAITHFUL timeframe-aligned model: mark the CRT +
# key level on the HIGHER timeframe, take the entry (sweep + single-candle CISD)
# on the aligned LOWER timeframe, SL beyond the swept LTF extreme, TP = C1 body.
# Beginner-recommended pairs only (the doc says master these before the risky
# sub-daily ones). Backtesting found no durable mechanical edge — this runs in
# PAPER as an honest forward-test alongside ICT; judge on the dashboard.
# A+ SCANNER: surfaces only high-quality CRT setups where MULTIPLE key levels
# stack (confluence), across ALL the higher timeframes, for the user to judge
# and enter manually. Backtesting found no mechanical edge — the value is a
# clean, filtered feed of the best setups; the human applies the judgement.
# CRT now fires as an HTF SETUP the moment a valid Candle Range Theory candle
# prints on a higher timeframe — the way the group actually trades it. Relaxed
# from the old strict aligned model (which almost never fired): ranges allowed
# (only a clearly opposite trend is rejected), confluence>=1 (not A+ 2), and NO
# lower-timeframe fill requirement — you get the alert when the setup forms and
# take the entry with your own judgement. Full textbook trade plan attached
# (enter at C2 close, stop = C2 protected extreme, target = C1 body, 2R-partial
# + BE runner). The old aligned engine (detect_crt_aligned) is retired from live.
# CRT is now the RESEARCH-VALIDATED enhanced DAILY model (Anees/Romeo sources,
# backtested step-by-step): classic CRT + C3 confirmation (wait for the next
# daily candle) + 50%/opposite-extreme targets + SMT (BTC/ETH divergence), with-
# trend, at a key level. Backtest: +0.25%/tr, 72% win, both walk-forward halves
# positive, broad across the universe. DAILY ONLY (4h/12h tested negative, no TF
# alignment). Paper forward-test. See memory/crt-enhancement-research.md.
ENABLE_CRT = True

# OTE / "Textbook Setup" (ICT-2022) — the SEPARATE 3rd strategy (distinct from
# ICT and CRT). Sweep -> displacement+MSS -> retrace into the 0.705-0.786 OTE
# band (limit) -> stop beyond the origin, target the next liquidity, with the
# group's 2R-partial/BE risk management. UNLIKE CRT, OTE has a walk-forward-
# robust edge in backtest — but ONLY with the risk overlay and ONLY on the
# mid/high timeframes (a measured signal-vs-noise gradient: sub-hourly is noise,
# 1h+ is clean; 12h strongest). Fires only when the limit actually fills on the
# last closed bar (detect_ote_live), so paper P&L tracks the backtest. Each
# config = (entry timeframe, HTF-bias filter or None): 1h and 12h run natively;
# 4h is gated by 12h structure (alignment rescues 4h's weak half). Still PAPER —
# an honest forward-test to see if the robust backtest edge holds live.
ENABLE_OTE = True
OTE_CONFIGS = [("1h", None), ("12h", None), ("4h", "12h")]

# OTE SETUP-SCANNER (strategy="OTE-Scan") — the WIDE discretionary feed. OTE's
# mechanical edge is only on 1h/12h (above), but the group trades the same
# textbook setup down on the lower timeframes across all coins, by hand. So on
# these TFs we ALERT the setups as MARKERS for the user's own judgement (the
# edge is the selection, not the raw signal) and track them under a SEPARATE
# label so the strict OTE scoreboard stays clean. Honest: these low-TF setups
# are NOT proven profitable on their own — they are a feed to apply discretion.
ENABLE_OTE_SCAN = True
OTE_SCAN_TFS = ["15m", "30m"]

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

ENABLE_TREND = False            # old EMA Trend strategy off (backtest: net loser)
ENABLE_MSS = False              # standalone MSS off (backtest: ~break-even +0.04%); ICT (sweep+MSS+FVG) is the edge
# NOTE: the trend-following "TrendMA" (dualcross 20/100 SMA) strategy was REMOVED
# 2026-08-06 — the lab's risk-adjusted "edge" never showed up in live paper
# trading (12 trades, one -32.9% blow-up on a too-wide stop); pulled entirely.
UNIVERSE_SIZE = 50              # top N by mcap (widened 40->50 per user) — applies to
                                # ALL strategies (ICT + CRT) via get_universe_ranked.

# Money-flow gate: only take a signal when the coin is in a real volume surge
# (latest volume >= FLOW_MULT x its 20-bar average) — "trade where money is
# flowing." The threshold is VENUE-DEPENDENT: 1.3x was tuned for binance.com's
# deep smooth volume, but the dashboard badge proved GitHub runners can't reach
# data-api.binance.vision and always fall back to binanceus, whose thin spiky
# volume clears 1.3x far too easily (gate too loose). 2.0x is the value that
# was validated on binanceus data (no-gate +1.28 -> 2x +1.84/trade, robust 2x/3x).
ENABLE_FLOW = True
FLOW_MULT = 2.0

# Volume Profile location filter (backtest: +0.36 -> ~+0.50-0.75/trade on majors,
# +1.54 -> +2.34 on the live universe; robust across 30/50/70 bins). Only take a
# LONG that enters at/below the volume POC (discount) or a SHORT at/above it
# (premium) — don't chase into where volume was already done. Cuts trade count
# ~2/3, so signals get rarer. Toggle here; backtester reads the same flags.
ENABLE_VP = True
VP_BINS = 50

# Strategy Health Monitor: pause coins whose RECENT live expectancy has decayed
# (see health_monitor.py). Adaptive + conservative-only — it can pause and later
# auto-recover a coin, never loosen risk. Live-only (needs accumulated results).
ENABLE_HEALTH = True


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


def _closed_df(coin, tf, per_tf):
    """Closed candles for a timeframe — reuse the main scan's fetch when the TF
    was already analysed, else fetch it (e.g. Monthly for the 1M->1d pair)."""
    r = per_tf.get(tf)
    if r is not None and r.get("df") is not None:
        return r["df"]
    candles = fetch_candles(coin, tf)
    if not candles or len(candles) < 35:
        return None
    return pd.DataFrame(
        candles, columns=["timestamp", "open", "high", "low", "close", "volume"]
    ).iloc[:-1]


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
    rel_vol = float(latest.volume / vol_sma) if (pd.notna(vol_sma) and vol_sma > 0) else None
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
            "rel_vol": rel_vol,
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

    # CRT runs as a timeframe-aligned pass in run_agent (needs HTF+LTF candles),
    # so stash the closed candles here for it to reuse (no extra fetches).
    result["df"] = closed

    return result


def passes_filters(s):
    """The rules that decide whether a coin is a tradeable signal."""
    # CRT and OTE are gated inside their own detectors (structure + key level +
    # valid setup / limit fill) — skip the ICT-oriented vol/VP/flow filters.
    if s["strategy"] in ("CRT", "OTE", "OTE-Scan"):
        return True

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
        # Money-flow gate: only trade coins in a real volume surge.
        if ENABLE_FLOW and s.get("rel_vol") is not None and s["rel_vol"] < FLOW_MULT:
            return False
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


# Plain-English translations of the SMC reasoning tags, so an alert reads like a
# sentence instead of shorthand.
PLAIN_TERMS = {
    # structure
    "BOS BULLISH": "uptrend continuing (broke above recent high)",
    "BOS BEARISH": "downtrend continuing (broke below recent low)",
    "CHoCH BULLISH": "may be turning up (first higher high)",
    "CHoCH BEARISH": "may be turning down (first lower low)",
    # sweeps / liquidity
    "Sell-side sweep": "dipped to grab stops below, then bounced",
    "Buy-side sweep": "spiked to grab stops above, then dropped",
    "Stop hunt below equal lows": "hunted stops below a support",
    "Stop hunt above equal highs": "hunted stops above a resistance",
    "Equal highs (liquidity above)": "resistance with stops sitting above it",
    "Equal lows (liquidity below)": "support with stops sitting below it",
    "Prev-day low sweep": "swept yesterday's low",
    "Prev-day high sweep": "swept yesterday's high",
    # gaps / zones
    "Bullish FVG": "left a price gap below to buy into",
    "Bearish FVG": "left a price gap above to sell into",
    "Bullish order block": "sitting on a strong buy zone",
    "Bearish order block": "sitting under a strong sell zone",
    "OB retest (mitigation)": "retesting a zone that's holding",
    "Breaker (bullish OB failed)": "old support flipped to resistance",
    "Breaker (bearish OB failed)": "old resistance flipped to support",
    # shift
    "MSS bullish": "structure flipped up (buyers took control)",
    "MSS bearish": "structure flipped down (sellers took control)",
    # order flow
    "CVD BULLISH": "buyers in control of volume",
    "CVD BEARISH": "sellers in control of volume",
    "CISD BULLISH": "momentum just turned up",
    "CISD BEARISH": "momentum just turned down",
}


def _plain(tag):
    """Turn an SMC tag into a plain-English phrase (leaves unknown tags as-is)."""
    return PLAIN_TERMS.get(tag, tag)


def run_agent():

    print("\n==============================")
    print("Scanning market...")
    print(datetime.now())
    print("==============================\n")

    # Live universe: blend of market cap + Binance 24h volume + sector heat
    # (liquidity/narrative). Fails soft to plain market-cap order.
    try:
        coins = universe.get_universe_ranked(exchange, limit=UNIVERSE_SIZE)
    except Exception as e:
        print(f"ranked universe failed ({type(e).__name__}); mcap fallback.")
        coins = universe.get_universe(exchange, limit=100)[:UNIVERSE_SIZE]
    bias = market_bias()

    # Narrative awareness: which sectors is money rotating into right now.
    heat = sector_flow.sector_heat(exchange)
    heat_line = "  ".join(f"{h['sector']} {h['avg_pct']:+.1f}%" for h in heat[:4]) or "n/a"
    print(
        f"Watching {len(coins)} coins (top {UNIVERSE_SIZE} by mcap) across "
        f"{len(TIMEFRAMES)} timeframes on {exchange.id}\n"
        f"Market bias (BTC daily): {bias}\n"
        f"Hot narrative: {heat_line}\n"
    )

    signals = []
    bar_map = {}
    # CRT SMT reference: BTC & ETH daily candles (fetched once per scan).
    crt_btc_1d = _closed_df("BTC/USDT", "1d", {}) if ENABLE_CRT else None
    crt_eth_1d = _closed_df("ETH/USDT", "1d", {}) if ENABLE_CRT else None
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

        # ----- CRT pass: the validated enhanced DAILY model (C3 confirmation +
        #  50%/opposite targets + SMT). Fires once per confirmed daily setup. -----
        if ENABLE_CRT:
            try:
                cdf = _closed_df(coin, "1d", per_tf)
                ref = crt_eth_1d if coin == "BTC/USDT" else crt_btc_1d
                if cdf is not None and ref is not None:
                    crt = detect_crt_enhanced(cdf, ref_df=ref)
                    if crt:
                        signals.append({
                            "coin": coin, "timeframe": "1d", "horizon": "Daily CRT",
                            "strategy": "CRT", "direction": crt["direction"],
                            "confidence": 80, "price": crt["entry"], "atr": 0.0,
                            "stop_level": crt["stop"], "target_level": crt["tp2"],
                            "tp1_level": crt["tp1"], "key_level": crt["key_level"],
                            "crt_conf": crt["confluence"], "htf": "1d", "ltf": "1d",
                            # signal_ts = the C3 (confirmation) daily candle.
                            "signal_ts": int(cdf["timestamp"].iloc[-1]),
                            "regime": "-", "quality": "STRONG", "rsi": 0.0,
                            "vol_confirm": True, "smc": "-", "smc_features": [],
                        })
            except Exception as e:
                print(f"Error CRT {coin}: {type(e).__name__}: {e}")

        # ----- OTE (Textbook Setup) pass: fire on the last-closed-bar limit fill,
        #  optionally gated by higher-timeframe structure (4h gated by 12h). -----
        if ENABLE_OTE:
            for etf, hbias_tf in OTE_CONFIGS:
                try:
                    edf = _closed_df(coin, etf, per_tf)
                    if edf is None:
                        continue
                    osig = detect_ote_live(edf)
                    if not osig:
                        continue
                    # Alignment filter: drop setups that fight the HTF draw.
                    if hbias_tf:
                        hdf = _closed_df(coin, hbias_tf, per_tf)
                        if hdf is not None:
                            bias = ote_htf_bias(hdf)
                            opp = "DOWN" if osig["direction"] == "LONG" else "UP"
                            if bias == opp:
                                continue
                    horizon = etf if not hbias_tf else f"{hbias_tf}-bias → {etf}"
                    signals.append({
                        "coin": coin, "timeframe": etf, "horizon": horizon,
                        "strategy": "OTE", "direction": osig["direction"],
                        "confidence": 80, "price": osig["entry"], "atr": 0.0,
                        "stop_level": osig["stop"], "target_level": osig["target"],
                        "key_level": "OTE 0.705-0.786 retrace", "htf": hbias_tf or "",
                        "ltf": etf, "ote_extreme": osig.get("extreme"),
                        "signal_ts": int(edf["timestamp"].iloc[-1]),  # fill candle
                        # defaults so shared print/alert paths don't KeyError
                        "regime": "-", "quality": "STRONG", "rsi": 0.0,
                        "vol_confirm": True, "smc": "-", "smc_features": [],
                    })
                except Exception as e:
                    print(f"Error OTE {coin} {etf}: {type(e).__name__}: {e}")

        # ----- OTE-Scan pass: the wide, lower-timeframe discretionary feed
        #  (markers for the user's judgement; tracked under its own label). -----
        if ENABLE_OTE_SCAN:
            for etf in OTE_SCAN_TFS:
                try:
                    edf = _closed_df(coin, etf, per_tf)
                    if edf is None:
                        continue
                    osig = detect_ote_live(edf)
                    if not osig:
                        continue
                    signals.append({
                        "coin": coin, "timeframe": etf, "horizon": f"{etf} scan",
                        "strategy": "OTE-Scan", "direction": osig["direction"],
                        "confidence": 75, "price": osig["entry"], "atr": 0.0,
                        "stop_level": osig["stop"], "target_level": osig["target"],
                        "key_level": "OTE 0.705-0.786 retrace", "htf": "",
                        "ltf": etf, "ote_extreme": osig.get("extreme"),
                        "signal_ts": int(edf["timestamp"].iloc[-1]),
                        "regime": "-", "quality": "STRONG", "rsi": 0.0,
                        "vol_confirm": True, "smc": "-", "smc_features": [],
                    })
                except Exception as e:
                    print(f"Error OTE-Scan {coin} {etf}: {type(e).__name__}: {e}")

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
            reason = t.get("reason")
            why = f"  ·  {reason}" if reason else ""
            strat = t.get("strategy", "")
            print(f"Trade closed: {t['coin']} {t['direction']} {tf} {mark} {t['pnl_pct']}% {reason or ''}")
            asyncio.run(send_alert(
                f"{emoji} {mark}  {t['coin']}  {t['direction']}  ({tf}) {strat}{why}\n"
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
    paused = health_monitor.paused_coins() if ENABLE_HEALTH else set()
    if paused:
        print(f"Health monitor: paused (decaying) coins skipped -> {sorted(paused)}")
    new_alerts = []   # only genuinely NEW positions get a Telegram ping
    for s in qualified:
        if open_count >= MAX_OPEN_TRADES:
            break
        if s["coin"] in paused:            # recent live expectancy decayed -> sit out
            continue
        if open_by_dir.get(s["direction"], 0) >= MAX_OPEN_PER_DIRECTION:
            continue

        trade = calculate_trade(
            s["price"], s["direction"], s["atr"], s["strategy"], s.get("stop_level"),
            s.get("target_level"), s.get("tp1_level"),
        )
        # open_trade returns False if a trade for this coin+direction+timeframe+
        # strategy is already open — so the SAME signal never re-fires, but the
        # same coin on a DIFFERENT timeframe opens (and alerts) separately.
        opened = paper_trading.open_trade(
            s["coin"], s["direction"],
            trade["entry"], trade["stop"], trade["tp1"], trade["tp2"],
            s["confidence"], s["timeframe"], s["strategy"],
            signal_ts=s.get("signal_ts"),
        )
        if opened:
            save_signal(
                s["coin"], s["direction"],
                trade["entry"], trade["stop"], trade["tp1"], trade["tp2"],
                s["confidence"], s["timeframe"], s["strategy"],
            )
            open_count += 1
            open_by_dir[s["direction"]] = open_by_dir.get(s["direction"], 0) + 1
            new_alerts.append((s, trade))

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
        best["price"], best["direction"], best["atr"], best["strategy"],
        best.get("stop_level"), best.get("target_level"), best.get("tp1_level"),
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

    # 5) Ping Telegram ONCE for each genuinely NEW position opened this scan.
    #    Dedup is by coin+direction+timeframe+strategy (open_trade), so the same
    #    signal never repeats while open, but the same coin on a different
    #    timeframe — or a different strategy — alerts separately.
    if new_alerts:
        blocks = []
        for s, tr in new_alerts:
            entry = tr["entry"]
            # Signed % move to a level (what you'd make/lose exiting there).
            def pct(level):
                r = (level - entry) / entry * 100 if entry else 0
                return r if s["direction"] == "LONG" else -r
            action = "🟢 BUY" if s["direction"] == "LONG" else "🔴 SELL"

            if s["strategy"] == "CRT":
                # Enhanced daily CRT card — confirmed setup + trade plan, % only.
                tp1, tp2, sl = tr["tp1"], tr["tp2"], tr["stop"]
                r1, r2, rsk = abs(pct(tp1)), abs(pct(tp2)), abs(pct(sl))
                side = "🟢 BUY (long)" if s["direction"] == "LONG" else "🔴 SELL (short)"
                trap = ("swept an old low then closed back inside — a liquidity grab"
                        if s["direction"] == "LONG"
                        else "swept an old high then closed back inside — a liquidity grab")
                conf = s.get("crt_conf", 1)
                b = [
                    f"📊 CRT (Daily) — {s['coin']}",
                    f"Daily · with-trend · {conf} key level"
                    + ("s" if conf != 1 else "") + " · SMT confirmed",
                    "",
                    f"{side}",
                    f"💵 Entry  {fmt_price(entry)}   ← C3 confirmation close",
                    f"🛑 Stop loss  {fmt_price(sl)}   (-{rsk:.1f}%)   ← beyond C2's swept extreme",
                    f"🎯 Target 1  {fmt_price(tp1)}   (+{r1:.1f}%)   ← 50% of the range · bank 50%, stop to break-even",
                    f"🏁 Target 2  {fmt_price(tp2)}   (+{r2:.1f}%)   ← opposite extreme of the range (runner)",
                    "",
                    "📋 The logic:",
                    f"   • Price {trap}",
                    "   • Next candle confirmed the reversal (C3 close)",
                    f"   • At a key level: {s.get('key_level', 'key level')}, with the trend",
                    "   • SMT: the reference (BTC/ETH) did not sweep — divergence in our favour",
                    "",
                    "📝 Paper forward-test (validated daily model) — no real money.",
                ]
                blocks.append("\n".join(b))
                continue

            if s["strategy"] in ("OTE", "OTE-Scan"):
                # OTE / Textbook Setup card — plain English, percentages.
                scan = s["strategy"] == "OTE-Scan"
                TFN = {"1M": "Monthly", "1w": "Weekly", "1d": "Daily", "4h": "4-hour",
                       "1h": "1-hour", "15m": "15-min", "5m": "5-min",
                       "12h": "12-hour", "30m": "30-min"}
                etf = TFN.get(s["timeframe"], s["timeframe"])
                hb = s.get("htf", "")
                where = (f"{etf} chart" if not hb
                         else f"{etf} chart · aligned with the {TFN.get(hb, hb)} trend")
                tp1, tp2, sl = tr["tp1"], tr["tp2"], tr["stop"]
                r1, r2, rsk = abs(pct(tp1)), abs(pct(tp2)), abs(pct(sl))
                side = "🟢 BUY (long)" if s["direction"] == "LONG" else "🔴 SELL (short)"
                trap = ("swept an old low then reversed up" if s["direction"] == "LONG"
                        else "swept an old high then reversed down")
                b = [
                    (f"🔎 OTE SCAN — {s['coin']}" if scan else f"🎯 OTE SETUP — {s['coin']}"),
                    (where + "  ·  lower-TF feed — your discretion" if scan else where),
                    "",
                    f"{side}",
                    f"💵 Entry (limit)  {fmt_price(entry)}   ← the 0.705–0.786 retrace (OTE zone)",
                    f"🛑 Stop loss      {fmt_price(sl)}   (-{rsk:.1f}%)   ← beyond the origin low/high",
                    f"🎯 Target 1       {fmt_price(tp1)}   (+{r1:.1f}%)   ← bank 50%, stop to break-even",
                    f"🏁 Target 2       {fmt_price(tp2)}   (+{r2:.1f}%)   ← runner to the next liquidity",
                    "",
                    "📋 The logic (Textbook Setup):",
                    f"   • Price {trap} — a liquidity grab",
                    "   • Then a strong displacement + market-structure shift (MSS)",
                    "   • Entry on the retrace into the 0.705–0.786 discount/premium zone",
                    "   • Target = the next resting liquidity",
                    "",
                    ("📝 Scanner marker — NOT auto-traded on edge; apply your own judgement."
                     if scan else
                     "📝 Paper forward-test — your call; risk ~1%, wait for the pullback fill."),
                ]
                blocks.append("\n".join(b))
                continue

            b = [
                f"{action}  {s['coin']}   ·   {s['confidence']}%",
                f"{s['horizon']} ({s['timeframe']}) · {s['strategy']}",
                "",
                f"💵 Entry   {fmt_price(entry)}",
                f"🛑 Stop    {fmt_price(tr['stop'])}   ({pct(tr['stop']):+.1f}%)",
                f"🎯 Target  {fmt_price(tr['tp1'])}  ({pct(tr['tp1']):+.1f}%)"
                f"  then  {fmt_price(tr['tp2'])}  ({pct(tr['tp2']):+.1f}%)",
            ]
            feats = s.get("smc_features") or []
            if s.get("smc", "-") != "-":
                feats = [s["smc"]] + feats
            if feats:
                plain = [f"• {_plain(t)}" for t in feats[:3]]
                b.append("📋 Why it fired:\n   " + "\n   ".join(plain))
            blocks.append("\n".join(b))

        header = f"⚡ {len(new_alerts)} NEW SIGNAL{'S' if len(new_alerts) > 1 else ''}"
        if heat:
            header += f"\nHot sectors: {heat_line}"
        message = header + "\n\n" + "\n\n———\n\n".join(blocks)
        asyncio.run(send_alert(message))
        print(message)
    else:
        print("No new positions this scan - nothing to alert (dedup working).")


# Loop forever only when run directly (python agent.py) — e.g. as a 24/7
# systemd service on the Oracle VM. On GitHub Actions we import run_agent()
# from scan_once.py instead, so this loop must NOT run there.
# Interval is configurable via CRYPTO_SCAN_INTERVAL (seconds); default 900 (15m).
if __name__ == "__main__":
    import os
    import run_health

    interval = int(os.environ.get("CRYPTO_SCAN_INTERVAL", "900"))

    while True:

        prev = run_health.load()
        prev_status = prev["last"]["status"] if prev and prev.get("last") else "ok"

        try:
            run_agent()
            stats = paper_trading.get_stats()
            run_health.record(
                "ok", open=stats["open"], closed=stats["closed"],
                win_rate=stats["win_rate"],
            )

        except Exception as e:
            print("Agent error:", e)
            run_health.record("error", error=f"{type(e).__name__}: {e}")
            # Ping Telegram only on the transition into failure (no spam if it
            # keeps failing) — otherwise a broken bot just goes silently stale.
            if prev_status == "ok":
                try:
                    asyncio.run(send_alert(f"⚠️ crypto-agent scan FAILED: {type(e).__name__}: {e}"))
                except Exception:
                    pass

        print(f"\nWaiting {interval // 60} minutes...\n", flush=True)

        time.sleep(interval)
