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
from strategies.smc.ict_model import detect_ict, detect_ict_source, detect_mss
from strategies.smc.crt import (
    detect_crt_aligned, detect_crt_setup, detect_crt_enhanced, detect_crt_scout,
    detect_crt_v3, ltf_confirms, detect_crt_10, CRT10_PAIRS)
import telegram_approve
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
# Auto daily-CRT model PAUSED: superseded by the human-approval CRT scanner
# below, so the "CRT" scoreboard reflects only the setups the user approves (not
# auto-opened ones). Flip back to True to resume the auto daily model in parallel.
ENABLE_CRT = False

# CRT (human-approval scanner) — the refined CRT. Each scan, across SCOUT_TFS, it
# finds a CRT on the last closed candle at key-level confluence (the user's own
# method: reversal direction, NO trend filter), saves it as a PENDING paper
# trade, and sends it to Telegram with Approve/Skip buttons. Approving flips it to
# a tracked OPEN trade under the "CRT" strategy; skipping drops it. Button presses
# are handled in real time by the separate telegram_approve listener service.
# SUPERSEDED 2026-08-12 by CRT v3 below. Measured over 36,253 candles, this
# detector's alerts were 60% not-a-CRT and it missed 97.9% of the real ones —
# it hunts a swing pivot first, which finds a different pattern. Turned off,
# not deleted: its open trades keep being managed to their TP/SL as normal.
ENABLE_CRT_SCOUT = False
SCOUT_TFS = ["1w", "1d", "4h"]
# Confluence gate: how many key levels (FVG / old high-low / rejection block) must
# STACK for an alert. 1 = any single key level (~30% of candles — the widest
# feed). 2 = A+ only (~6%, far fewer). Tune here without touching anything else.
SCOUT_MIN_CONFLUENCE = 1
# Minimum reward:risk (entry->TP2 vs entry->stop) for a setup to be proposed.
# 1.0 = never propose a sub-1:1 trade.
SCOUT_MIN_RR = 1.0
SCOUT_STRATEGY = "CRT"
# Smallest stop we will accept, PER TIMEFRAME. A stop closer than this to the
# entry gets tagged by ordinary noise rather than by the trade being wrong.
#
# This used to be one flat 1.5% for every timeframe, which was a mistake: a
# typical stop is ~0.9% on 1h and ~1.2% on 4h but ~3% on 1d, so the flat figure
# threw away 3 of every 4 hourly setups and 2 of every 3 four-hour ones while
# barely touching daily. Measured across 364 coin/timeframe frames.
SCOUT_MIN_STOP_PCT = {"1w": 0.015, "1d": 0.015, "4h": 0.006, "1h": 0.0045}
SCOUT_MIN_STOP_DEFAULT = 0.015

# ---- CRT v3: the rebuilt detector ----------------------------------------
# The scout above finds a swing pivot first and only then asks whether a candle
# swept it. Over 36,253 candles that proved to be a different pattern: 60% of
# its alerts are not a CRT at all and it misses 97.9% of the real ones. v3 puts
# the order back the way it is taught — find the CRT, then let the key level
# qualify it. Per-timeframe stops, R:R and (on 1h) a minimum payout live in
# strategies/smc/crt.py so the detector and any backtest cannot disagree.
ENABLE_CRT_V3 = False            # superseded by CRT 1.0 (2026-08-13). Its rows
                                 # and open trades are untouched and still
                                 # managed; only NEW signals stop, so the two
                                 # feeds can't double-alert the same setup.
# 1h dropped 2026-08-13: it was the weakest timeframe in the backtest
# (-0.34%/trade vs -0.15% on 4h) and 20 of its first 31 live setups expired
# before they could be approved — an hourly move covers 30% of its range too
# fast to review. The per-timeframe stop/RR settings for it are kept below so
# it can be switched back on by adding it here.
CRT_V3_TFS = ["1w", "1d", "4h"]
CRT_V3_MIN_CONFLUENCE = 1        # 1 = any key level qualifies; >1 is flagged A+
CRT_V3_STRATEGY = "CRT"

# The practice feed: every real CRT on the higher timeframes with NO key-level
# requirement, clearly labelled, so the level call is yours to make. Higher
# volume by design (~33/day) — it is for training the eye, not for trading.
ENABLE_CRT_RAW = False           # off with v3 (2026-08-13); rows untouched
CRT_RAW_TFS = ["1w", "1d", "4h"]
CRT_RAW_STRATEGY = "CRT raw"

# How far a setup you haven't tapped yet may run before it is retired. At 0.30
# a third of the move to the first target has already happened without you, so
# the reward left no longer justifies the same stop.
# ----- CRT 1.0 — the specification recovered from the user's course videos.
#  Marked on the higher timeframe, ENTERED on the aligned lower one. This is the
#  only version of CRT the project has measured as positive:
#    1d->1h  +0.498%/tr  t=+5.50  halves +0.497/+0.498  65/99 coins
#    1w->4h  +1.055%/tr  t=+2.84  halves +1.03/+1.08
#  (1M->1d and 4h->15m both measured NEGATIVE and are kept ON at the user's
#   request, to be judged by eye over the next few weeks before deciding.
#   15m->1m was dropped outright: -0.069%/tr and only 28 days of data.)
#  Still human-approval: every setup waits for the Telegram tap.
ENABLE_CRT_10 = True
CRT_10_TFS = ["1M", "1w", "1d", "4h"]     # analysis TFs; entry TF comes from
                                          # crt.CRT10_PAIRS
CRT_10_MIN_CONFLUENCE = 1
CRT_10_STRATEGY = "CRT 1.0"

PENDING_EXPIRE_FRAC = 0.30

# Which lower timeframe to check for confirmation of a higher-timeframe CRT.
# Purely informational — it is reported on the alert and never blocks one.
CRT_LTF = {"1w": "4h", "1d": "1h", "4h": "15m", "1h": "15m"}
# Timeframe weight for resolving a same-coin LONG-vs-SHORT clash in one scan
# (higher timeframe wins; confluence breaks ties).
SCOUT_TF_WEIGHT = {"1w": 3, "1d": 2, "4h": 1, "1h": 0}

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
# OTE turned OFF (user decision 2026-08-09): the setup is real but ultra-rare
# (~4 per coin per year) so it opened 0 live trades — not worth a strategy slot.
ENABLE_OTE = False
OTE_CONFIGS = [("1h", None), ("12h", None), ("4h", "12h")]

# OTE SETUP-SCANNER (strategy="OTE-Scan") — the WIDE discretionary feed. OTE's
# mechanical edge is only on 1h/12h (above), but the group trades the same
# textbook setup down on the lower timeframes across all coins, by hand. So on
# these TFs we ALERT the setups as MARKERS for the user's own judgement (the
# edge is the selection, not the raw signal) and track them under a SEPARATE
# label so the strict OTE scoreboard stays clean. Honest: these low-TF setups
# are NOT proven profitable on their own — they are a feed to apply discretion.
ENABLE_OTE_SCAN = False   # OFF with OTE (unproven low-TF feed; reduce clutter)
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
# BTC-REGIME-GATED TREND-FOLLOWING (strategy="TrendGated") — the validated crypto
# edge (2026-08-09). Hold an alt LONG only while the alt AND BTC are both above
# their daily SMA(TREND_SMA); exit the moment either flips. Long/flat, NO stop
# (exits by signal — the flaw that blew up the old Trend strategy is gone).
# Walk-forward + fees: beat buy-and-hold on return, Sharpe AND drawdown in every
# window (full +4728%/0.82 Sharpe vs +2120%/0.67; lower DD throughout). Paper.
ENABLE_TREND_GATED = False      # DISABLED 2026-08-10 per user — auto trades cluttered the CRT view; keep CRT + ICT only
TREND_SMA = 50
TREND_TF = "1d"
# PAUSED 2026-08-11 while the model is rebuilt from the ICT source lectures.
# Measured on 1,856 paired historical setups, the current entry/stop loses
# -1.02%/trade (t=-6.6; -1.85%/tr in the recent half). The source places the
# entry as a LIMIT at the fair-value-gap edge and the stop just beyond the gap,
# which tests +1.32%/trade better on the SAME setups. No new ICT trades open
# while this is False; trades already open keep being managed to their TP/SL.
ENABLE_ICT = False
# "ICT new" (2026-08-11) — the 2022 ICT model rebuilt from the source lectures and
# run as a HUMAN-APPROVAL scout, exactly like CRT: it proposes, you decide.
# Verified on 2,359 historical setups across the live universe: +0.70%/trade
# (t=+5.9) vs the old auto-ICT's -1.02%, positive in BOTH walk-forward halves,
# profitable on 65% of coins, and positive at every parameter setting tested and
# at triple fees. Deliberately labelled separately from the legacy "ICT" rows so
# this forward test is judged on its own record.
# CAVEAT worth remembering: the profit sits in the top ~10% of trades, so losing
# streaks will feel worse than the average suggests.
# PARKED 2026-08-13 at the user's request, along with the legacy ICT rows,
# which were deleted from the database so the scoreboard shows only CRT. The
# detector and its settings are untouched — flip this back to True to resume
# the forward test, and the backtest that justified it is in the memory notes.
ENABLE_ICT_SCOUT = False
ICT_SCOUT_TFS = ["4h", "1d"]     # 1w had only 12 historical setups — too few
ICT_SCOUT_STRATEGY = "ICT new"
# USABILITY gates, NOT edge improvements — be honest about this. Unfiltered
# scores best statistically (+0.701%/tr, t=+5.9); these two cost a little of that
# (+0.696%, t=+3.3) and roughly halve the alert volume, in exchange for never
# proposing a trade that risks 4% to make 0.4%, or one whose stop sits inside the
# noise. Same thresholds as the CRT scout, so both feeds behave consistently.
# Min R:R 1.1 set by the user. Tested: 1.0 -> +0.620%/tr (t=3.3), 1.1 -> +0.557%
# (t=2.8), 1.2 -> +0.679% (t=3.2) — the differences between them are noise, all
# fine. But raising it FURTHER actively BREAKS it (>=2.0 turns the recent half
# negative) — do not tighten past ~1.5 without re-running the test.
# The CRT odd-price placement was tested here and REJECTED: -0.183%/tr, t=-6.5.
# It lifts the win rate (54.6->58.0%) but loses money — CRT stops sit on sweep
# wicks that really are hunted, ICT stops sit on gap edges that are not.
ICT_SCOUT_MIN_RR = 1.1
ICT_SCOUT_MIN_STOP_PCT = 0.015
# ICT is LONG-ONLY (2026-08-09): its shorts were a disaster live (18% win,
# -1.28%/tr) and in backtest (long-only +2.57%/tr @59% vs both-dirs +2.13%@47%).
# The daily-trend gate + retrace entry both tested WORSE, so we just cut shorts.
ICT_LONG_ONLY = True
# Skip an ICT trade whose stop already sits > this % from entry (the move
# over-extended before we'd enter = chasing your "already-run" concern). Backtest
# (long-only): 8% is the sweet spot — cut one over-extended loser, +2.83%/tr @
# 62.5% vs +2.57%@59% uncapped; 3-5% too tight, 12% no effect.
ICT_MAX_STOP_PCT = 8.0
# NOTE: the trend-following "TrendMA" (dualcross 20/100 SMA) strategy was REMOVED
# 2026-08-06 — the lab's risk-adjusted "edge" never showed up in live paper
# trading (12 trades, one -32.9% blow-up on a too-wide stop); pulled entirely.
def _load_pinned(path="pinned_universe.txt"):
    """User's watchlist coins (COIN/USDT per line) always added to the universe."""
    try:
        with open(path) as f:
            return [ln.strip() for ln in f if ln.strip() and "/" in ln]
    except OSError:
        return []


PINNED_COINS = _load_pinned()   # ~139 coins from the user's 11 TradingView lists

# Minimum 24h quote volume (USDT) for a PINNED watchlist coin to be scanned — the
# liquidity floor / "confidence layer": top-N by mcap are always in; the user's
# extra watchlist coins only join if they trade above this (drops dead small-caps).
MIN_PINNED_VOL_USD = 500_000
UNIVERSE_SIZE = 80              # top N by mcap (40->50->60->80 per user) — applies to
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


def _above_sma(df, length):
    """True/False if the last CLOSED candle's close is above/below its rolling
    SMA(length); None if there isn't enough data."""
    if df is None or len(df) < length + 1:
        return None
    sma = df["close"].rolling(length).mean().iloc[-1]
    if pd.isna(sma):
        return None
    return float(df["close"].iloc[-1]) > float(sma)


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
    ict = detect_ict(closed) if ENABLE_ICT else None
    if ict and (not ICT_LONG_ONLY or ict["direction"] == "LONG"):
        entry = float(latest.close)
        stop_dist = abs(entry - ict["swept"]) / entry * 100.0 if entry else 1e9
        if ICT_MAX_STOP_PCT is None or stop_dist <= ICT_MAX_STOP_PCT:
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


def _report_approval_feeds(scout_count, ict_scout_count, crt10_count=0):
    """Print how many setups each approval feed proposed this scan.

    Called from ONE place, above the 'no valid signals' early return, because
    these feeds run in the coin loop and have nothing to do with the auto
    strategies — reporting them after that return hid a working feed."""
    if ENABLE_CRT_10:
        waiting = paper_trading.count_pending()
        print(f"CRT 1.0: {crt10_count} new setup(s) sent for approval this scan"
              f" — {waiting} awaiting your tap.")
    if ENABLE_CRT_SCOUT or ENABLE_CRT_V3 or ENABLE_CRT_RAW:
        waiting = paper_trading.count_pending()
        print(f"CRT: {scout_count} new setup(s) sent for approval this scan"
              f" — {waiting} awaiting your tap.")
    if ENABLE_ICT_SCOUT:
        print(f"ICT new: {ict_scout_count} new setup(s) sent for approval this scan.")


def _pct_from(entry, price):
    """Distance from entry as a signed percentage — so every level on the alert
    says how far away it actually is, not just where it sits."""
    if not entry:
        return ""
    return f"{(price - entry) / entry * 100:+.2f}%"


def _scout_alert_text(coin, tf, s, unconfirmed=False, ltf=None):
    """Compact, scannable Telegram body for a CRT setup awaiting approval.

    Every price carries its distance from entry, because "TP2 62657" means
    nothing on its own — "+1.75%" is the number you actually judge.
    """
    risk = abs(s["entry"] - s["stop"])
    rr = s.get("rr") or (abs(s["tp1"] - s["entry"]) / risk if risk else 0.0)
    arrow = "🟢 LONG" if s["direction"] == "LONG" else "🔴 SHORT"
    e = s["entry"]

    conf = s["confluence"]
    if unconfirmed:
        # A practice sighting rather than a graded setup — but it still gets the
        # full plan. Without a stop and targets there is nothing to judge the
        # level against, which is the whole point of practising on it.
        swept = "high" if s["direction"] == "SHORT" else "low"
        head = f"🕯 <b>CRT · no confirmation</b> · <b>{coin}</b> · {tf}  {arrow}"
        note = (f"Swept the CRT {swept} ({s['crt_high']:.6g} / {s['crt_low']:.6g}) "
                f"and closed back inside.\n"
                f"⚠️ No key level checked — judge the level yourself")
    else:
        head = f"📊 <b>CRT</b> · <b>{coin}</b> · {tf}  {arrow}"
        note = (f"⭐ <b>A+</b> · {conf} key levels: {s['key_level']}"
                if conf > 1 else f"🔑 {conf} key level: {s['key_level']}")

    lines = [
        head,
        f"<code>Entry {e:.6g}</code>",
        f"<code>Stop  {s['stop']:.6g}  ({_pct_from(e, s['stop'])})</code>",
        f"<code>TP1   {s['tp1']:.6g}  ({_pct_from(e, s['tp1'])})</code>",
        f"<code>TP2   {s['tp2']:.6g}  ({_pct_from(e, s['tp2'])})</code>",
        note,
    ]
    if s.get("net_pct") is not None:
        lines.append(f"R:R ≈ {rr:.2f}  ·  booked out in full ≈ {s['net_pct']:.2f}%")
    else:
        lines.append(f"R:R ≈ {rr:.2f}")
    # Trend on THIS timeframe (his step one: "Daily is the trend"). Shown, never
    # gated — the backtest could not prove the gate, and CRT stays alerts-only
    # so the judgement is the user's.
    trend = s.get("trend")
    if trend:
        if trend == "MIXED":
            lines.append("📈 trend: no clear structure (he would skip this)")
        else:
            lines.append(f"📈 trend: {trend} — "
                         + ("✅ with trend" if s.get("with_trend")
                            else "⚠️ AGAINST trend (he would not trade it)"))
    if ltf is not None:
        lines.append("✅ lower timeframe confirmed" if ltf
                     else "⏳ no lower-timeframe confirmation yet")
    return "\n".join(lines)


#  Measured per-pairing performance, shown on the alert so the expectation is
#  set honestly: this is a fat-tail model (about 1 trade in 5 wins, and the top
#  10% of trades carry ~73% of the gains), so losing runs are normal.
CRT10_STATS = {
    "1w": "1w→4h backtest: +1.06%/trade, beats holding by +1.2%",
    "1d": "1d→1h backtest: +0.50%/trade, beats holding by +1.0%",
    "1M": "⚠️ 1M→1d backtest NEGATIVE but only 44 trades / 19 coins — and the "
          "course's biggest example (PROM ~15R) is a monthly CRT. Unresolved.",
    "4h": "⚠️ 4h→15m backtest: NEGATIVE (−0.10%/trade) — watch only",
}


def _crt10_alert_text(coin, htf, s):
    """Telegram body for a CRT 1.0 setup. The entry is a LIMIT on the lower
    timeframe — price has to come back to the retest level to fill it."""
    e = s["entry"]
    arrow = "🔴 SHORT" if s["direction"] == "SHORT" else "🟢 LONG"
    head = ("⭐ <b>CRT 1.0 · A+</b>" if s.get("aplus") else "📊 <b>CRT 1.0</b>")
    lines = [
        f"{head} · <b>{coin}</b> · {htf} → {s['ltf']}  {arrow}",
        f"<code>Entry {e:.6g}  (limit on {s['ltf']})</code>",
        f"<code>Stop  {s['stop']:.6g}  ({_pct_from(e, s['stop'])})</code>",
        f"<code>TP1   {s['tp1']:.6g}  ({_pct_from(e, s['tp1'])})</code>",
        f"<code>TP2   {s['tp2']:.6g}  ({_pct_from(e, s['tp2'])})</code>",
        f"🔑 {s['confluence']} key level: {s['key_level']}",
        f"<code>CRT   {s['crt_low']:.6g} — {s['crt_high']:.6g}  ({htf} range)</code>",
        f"<code>C1body {s['body_low']:.6g} — {s['body_high']:.6g}  (targets)</code>",
        f"<code>Swept {s['swept']:.6g}  ({s['ltf']} liquidity taken)</code>",
        f"<code>CISD  {s['cisd_line']:.6g}  (close beyond this = trigger)</code>",
        f"🕯 CISD {s['cisd_bars']} candle(s) after the sweep on {s['ltf']}",
        f"R:R ≈ {s['rr']:.2f}  ·  booked out in full ≈ {s['net_pct']:.2f}%",
    ]
    if s.get("leg_fvg"):
        lines.append("⚡ the reversal left an FVG — violent bounce (A+)")
    if s.get("trend"):
        lines.append(f"📈 trend: {s['trend']}"
                     + ("" if s["trend"] == "MIXED"
                        else ("  ✅ with trend" if s.get("with_trend")
                              else "  ⚠️ against trend")))
    lines.append(CRT10_STATS.get(htf, ""))
    return "\n".join(x for x in lines if x)


def _ict_alert_text(coin, tf, s):
    """Telegram body for an ICT-FVG setup awaiting approval. The entry is a LIMIT
    at the gap, so it is spelled out — price has to come back to fill it."""
    risk = abs(s["entry"] - s["stop"])
    return (
        f"📐 <b>ICT new</b> · <b>{coin}</b> · {tf}  🟢 LONG\n"
        f"<code>Entry {s['entry']:.6g}  (limit — waits for the retrace)</code>\n"
        f"<code>Stop  {s['stop']:.6g}  ({risk/s['entry']*100:.1f}%)</code>\n"
        f"<code>TP1   {s['tp1']:.6g}</code>\n"
        f"<code>TP2   {s['tp2']:.6g}</code>\n"
        f"🔑 {s['key_level']}\n"
        f"R:R ≈ {s['rr']:.1f}"
    )


def _emit(text, reply_to=None):
    """Send a Telegram message; if reply_to (a message_id) is given, thread it as
    a REPLY to that alert (via the requests-based sender) so the original setup is
    tagged. Else use the normal broadcast alert."""
    if reply_to:
        telegram_approve.send_message(text, reply_to=reply_to)
    else:
        asyncio.run(send_alert(text))


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
    # Pinned coins (the user's watchlists) join the ranked top-N ONLY if they
    # clear the liquidity floor (>= MIN_PINNED_VOL_USD 24h volume) — the extra
    # confidence layer. Deduped. Fails soft to all-pinned if volume unavailable.
    if PINNED_COINS:
        try:
            tk = exchange.fetch_tickers()
            pinned_liquid = [c for c in PINNED_COINS if c not in coins and
                             float((tk.get(c) or {}).get("quoteVolume") or 0) >= MIN_PINNED_VOL_USD]
        except Exception as e:
            print(f"pinned volume filter failed ({type(e).__name__}); adding all pinned.")
            pinned_liquid = [c for c in PINNED_COINS if c not in coins]
        coins = coins + pinned_liquid
        print(f"Universe: top {UNIVERSE_SIZE} by mcap + {len(pinned_liquid)} liquid "
              f"pinned (>= ${MIN_PINNED_VOL_USD/1e6:.0f}M/24h) = {len(coins)} coins")
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
    scout_count = 0        # CRT-Scout setups proposed for approval this scan
    ict_scout_count = 0    # "ICT new" setups proposed for approval this scan
    crt10_count = 0        # CRT 1.0 setups proposed for approval this scan
    trend_daily = {}       # coin -> closed daily df (for the BTC-gated trend pass)
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

        if ENABLE_TREND_GATED:
            trend_daily[coin] = _closed_df(coin, TREND_TF, per_tf)

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

        # ----- CRT-Scout pass: propose a CRT-at-key-level setup for HUMAN approval
        #  (the user's own method, reversal direction, no trend filter). Saves a
        #  PENDING paper trade + sends Approve/Skip buttons; never auto-opens. -----
        if ENABLE_CRT_SCOUT:
            found = []                                  # (tf, setup) across scout TFs
            for stf in SCOUT_TFS:
                try:
                    sdf = _closed_df(coin, stf, per_tf)
                    if sdf is None:
                        continue
                    setup = detect_crt_scout(
                        sdf, min_confluence=SCOUT_MIN_CONFLUENCE, min_rr=SCOUT_MIN_RR,
                        min_stop_pct=SCOUT_MIN_STOP_PCT.get(stf, SCOUT_MIN_STOP_DEFAULT))
                    if setup:
                        found.append((stf, setup))
                except Exception as e:
                    print(f"Error Scout {coin} {stf}: {type(e).__name__}: {e}")
            # Sanity: never propose the same coin LONG *and* SHORT at once. If both
            # directions appear across TFs, keep only the direction of the
            # strongest setup (higher timeframe first, then higher confluence).
            if found:
                strongest = max(found, key=lambda x: (
                    SCOUT_TF_WEIGHT.get(x[0], 0), x[1]["confluence"]))
                keep_dir = strongest[1]["direction"]
                for stf, setup in found:
                    if setup["direction"] != keep_dir:
                        continue                        # drop the conflicting side
                    try:
                        pid = paper_trading.create_pending(
                            coin, setup["direction"], setup["entry"], setup["stop"],
                            setup["tp1"], setup["tp2"], setup["confluence"], stf,
                            SCOUT_STRATEGY, signal_ts=setup["signal_ts"])
                        if pid is None:
                            continue                    # already proposed this candle
                        mid = telegram_approve.send_approval(pid, _scout_alert_text(coin, stf, setup))
                        if mid:
                            paper_trading.set_alert_msg(pid, mid)   # thread later TP/loss replies
                            scout_count += 1
                    except Exception as e:
                        print(f"Error Scout {coin} {stf}: {type(e).__name__}: {e}")

        # ----- CRT 1.0: marked on the HTF, ENTERED on the aligned LTF. -----
        if ENABLE_CRT_10:
            for htf in CRT_10_TFS:
                try:
                    hdf = _closed_df(coin, htf, per_tf)
                    if hdf is None:
                        continue
                    ltf_tf = CRT10_PAIRS.get(htf)
                    if not ltf_tf:
                        continue
                    # The LTF pull only happens once an HTF CRT exists, so quiet
                    # scans cost nothing extra.
                    if detect_crt_v3(hdf, tf=htf,
                                     min_confluence=CRT_10_MIN_CONFLUENCE) is None:
                        continue
                    ldf = _closed_df(coin, ltf_tf, per_tf)
                    if ldf is None:
                        continue
                    setup = detect_crt_10(hdf, ldf, htf,
                                          min_confluence=CRT_10_MIN_CONFLUENCE)
                    if not setup:
                        continue
                    pid = paper_trading.create_pending(
                        coin, setup["direction"], setup["entry"], setup["stop"],
                        setup["tp1"], setup["tp2"], setup["confluence"], htf,
                        CRT_10_STRATEGY, signal_ts=setup["signal_ts"])
                    if pid is None:
                        continue            # already proposed on this candle
                    mid = telegram_approve.send_approval(
                        pid, _crt10_alert_text(coin, htf, setup))
                    if mid:
                        paper_trading.set_alert_msg(pid, mid)
                    crt10_count += 1
                except Exception as e:
                    print(f"Error CRT1.0 {coin} {htf}: {type(e).__name__}: {e}")

        # ----- CRT v3 pass: the CRT is the trigger, the key level qualifies it.
        #  Two streams. The QUALIFIED one needs key levels stacked and is the
        #  feed to trade from. The RAW one is every real CRT on the higher
        #  timeframes with no level check, clearly labelled, for practising the
        #  level call yourself. Both still need your approval. -----
        for v_on, v_tfs, v_conf, v_strat, v_raw in (
                (ENABLE_CRT_V3, CRT_V3_TFS, CRT_V3_MIN_CONFLUENCE,
                 CRT_V3_STRATEGY, False),
                (ENABLE_CRT_RAW, CRT_RAW_TFS, 0, CRT_RAW_STRATEGY, True)):
            if not v_on:
                continue
            for vtf in v_tfs:
                try:
                    vdf = _closed_df(coin, vtf, per_tf)
                    if vdf is None:
                        continue
                    setup = detect_crt_v3(vdf, tf=vtf, min_confluence=v_conf)
                    if not setup:
                        continue
                    # The practice feed is for the CRTs that did NOT qualify.
                    # Without this every A+ setup would arrive twice — once to
                    # trade and once to practise on.
                    if (v_raw and ENABLE_CRT_V3 and vtf in CRT_V3_TFS
                            and setup["confluence"] >= CRT_V3_MIN_CONFLUENCE):
                        continue
                    # Lower-timeframe confirmation is only looked up once a
                    # setup exists, so it costs nothing on a quiet scan.
                    ltf = None
                    ltf_tf = CRT_LTF.get(vtf)
                    if ltf_tf:
                        try:
                            ltf = ltf_confirms(_closed_df(coin, ltf_tf, per_tf),
                                               setup["direction"],
                                               setup["signal_ts"])
                        except Exception:
                            ltf = None
                    pid = paper_trading.create_pending(
                        coin, setup["direction"], setup["entry"], setup["stop"],
                        setup["tp1"], setup["tp2"], setup["confluence"], vtf,
                        v_strat, signal_ts=setup["signal_ts"])
                    if pid is None:
                        continue                # already proposed this candle
                    mid = telegram_approve.send_approval(
                        pid, _scout_alert_text(coin, vtf, setup,
                                               unconfirmed=v_raw, ltf=ltf))
                    if mid:
                        paper_trading.set_alert_msg(pid, mid)
                        scout_count += 1
                except Exception as e:
                    print(f"Error CRTv3 {coin} {vtf}: {type(e).__name__}: {e}")

        # ----- "ICT new" pass: the source-faithful 2022 model, proposed for HUMAN
        #  approval like CRT. Entry is a LIMIT at the fair value gap, so approving
        #  it usually creates a WAITING trade that fills only if price returns. -----
        if ENABLE_ICT_SCOUT:
            for itf in ICT_SCOUT_TFS:
                try:
                    idf = _closed_df(coin, itf, per_tf)
                    if idf is None:
                        continue
                    setup = detect_ict_source(idf)
                    if not setup:
                        continue
                    stop_pct = abs(setup["entry"] - setup["stop"]) / setup["entry"]
                    if (setup["rr"] < ICT_SCOUT_MIN_RR
                            or stop_pct < ICT_SCOUT_MIN_STOP_PCT):
                        continue                        # usability gate, see config
                    pid = paper_trading.create_pending(
                        coin, setup["direction"], setup["entry"], setup["stop"],
                        setup["tp1"], setup["tp2"], setup["confluence"], itf,
                        ICT_SCOUT_STRATEGY, signal_ts=setup["signal_ts"])
                    if pid is None:
                        continue                        # already proposed this candle
                    mid = telegram_approve.send_approval(
                        pid, _ict_alert_text(coin, itf, setup))
                    if mid:
                        paper_trading.set_alert_msg(pid, mid)
                        ict_scout_count += 1
                except Exception as e:
                    print(f"Error ICT new {coin} {itf}: {type(e).__name__}: {e}")

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

    # 0) Retire un-tapped setups whose move has already gone without them, so a
    #    stale proposal can never be approved into a trade that has half
    #    happened. Runs before anything else touches the book.
    for g in paper_trading.expire_stale_pending(bar_map, frac=PENDING_EXPIRE_FRAC):
        print(f"Expired pending {g['strategy']} {g['coin']} {g['timeframe']}"
              f" — {g['reason']}")

    # 1) Advance paper trades (partial-exit plan); ping Telegram for each event
    #    — a partial bank at TP1, or a full close (WIN/LOSS/EXPIRED).
    for t in paper_trading.update_open_trades(bar_map):
        mark = t["result"]
        emoji = {"WIN": "✅", "LOSS": "❌", "EXPIRED": "⌛", "TP1": "🎯"}.get(mark, "❌")
        tf = t.get("timeframe", "")
        rt = t.get("alert_msg_id")   # reply to the original setup alert if we have it
        if mark == "TP1":
            print(f"TP1 hit (half banked): {t['coin']} {t['direction']} {tf} +{t['pnl_pct']}%")
            _emit(f"{emoji} TP1 HIT — half banked  {t['coin']}  {t['direction']}  ({tf})\n"
                  f"Locked: +{t['pnl_pct']}%  ·  runner to TP2, stop at break-even", reply_to=rt)
        elif mark == "FILLED":
            strat = t.get("strategy", "")
            print(f"Limit filled: {t['coin']} {t['direction']} {tf} — now tracking")
            _emit(f"▶️ FILLED — now tracking  {t['coin']}  {t['direction']}  ({tf}) {strat}", reply_to=rt)
        elif mark == "CANCELLED":
            strat = t.get("strategy", "")
            print(f"Limit cancelled (unfilled): {t['coin']} {t['direction']} {tf}")
            _emit(f"🚫 CANCELLED — limit never filled  {t['coin']}  {t['direction']}  ({tf}) {strat}",
                  reply_to=rt)
        else:
            reason = t.get("reason")
            why = f"  ·  {reason}" if reason else ""
            strat = t.get("strategy", "")
            print(f"Trade closed: {t['coin']} {t['direction']} {tf} {mark} {t['pnl_pct']}% {reason or ''}")
            _emit(f"{emoji} {mark}  {t['coin']}  {t['direction']}  ({tf}) {strat}{why}\n"
                  f"Result: {t['pnl_pct']}%", reply_to=rt)

    # 1b) BTC-regime-gated trend-following: manage LONG/FLAT trend positions.
    #     Hold an alt while alt>SMA & BTC>SMA (both on the daily); exit on either
    #     flip. Signal changes only when a new daily candle closes, so this is
    #     quiet intraday (dedup stops re-opening the same position).
    if ENABLE_TREND_GATED:
        try:
            btc_df = trend_daily.get("BTC/USDT")
            if btc_df is None:
                btc_df = _closed_df("BTC/USDT", TREND_TF, {})
            btc_bull = _above_sma(btc_df, TREND_SMA)
            opened = closed = 0
            # EXITS first: close any open trend whose alt fell below SMA, or BTC
            # regime turned off.
            for t in paper_trading.get_open_trends():
                adf = trend_daily.get(t["coin"])
                if adf is None:
                    adf = _closed_df(t["coin"], TREND_TF, {})
                if adf is None:
                    continue
                alt_bull = _above_sma(adf, TREND_SMA)
                if btc_bull is False or alt_bull is False:
                    res = paper_trading.close_trend(t["id"], float(adf["close"].iloc[-1]))
                    if res:
                        closed += 1
                        print(f"Trend EXIT {t['coin']} {res['result']} {res['pnl_pct']}%")
            # ENTRIES: only when BTC is bullish, open flat alts that are above SMA.
            if btc_bull:
                held = {t["coin"] for t in paper_trading.get_open_trends()}
                for coin in coins:
                    if coin in held or coin == "BTC/USDT":
                        continue
                    adf = trend_daily.get(coin)
                    if adf is not None and _above_sma(adf, TREND_SMA):
                        if paper_trading.open_trend(
                                coin, float(adf["close"].iloc[-1]), TREND_TF,
                                signal_ts=int(adf["timestamp"].iloc[-1])):
                            opened += 1
            if opened or closed:
                regime = "BTC bullish" if btc_bull else "BTC bearish"
                asyncio.run(send_alert(
                    f"📈 Trend ({regime}) — opened {opened}, closed {closed} "
                    f"BTC-gated alt-trend position(s)."))
                print(f"TrendGated: opened {opened}, closed {closed} (btc_bull={btc_bull})")
        except Exception as e:
            print(f"Error TrendGated: {type(e).__name__}: {e}")

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

    # Report the approval feeds BEFORE the early return below — they run in the
    # coin loop and are independent of the auto-strategy signals, so hiding
    # their counts on a quiet scan made a working feed look dead.
    _report_approval_feeds(scout_count, ict_scout_count, crt10_count)

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
