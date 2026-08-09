"""CRT (Candle Range Theory) backtester — a faithful, mechanical test of the
strategy taught in the CRT video series (C1 range -> C2 sweep + close back in ->
C3 delivers the C1-body target), with the taught layers testable as toggles.

This is a SEPARATE, read-only lab tool. It does NOT touch the live bot or DB.
It reuses the same candle cache (data/bt_cache), fee model, and universe as
backtest.py, so CRT numbers are directly comparable to ICT's.

The strategy, reduced to code
----------------------------
For each pair of closed candles C1 (prior) and C2 (current), evaluated at C2:
  Bearish CRT (SHORT):  C2.high > C1.high  (swept C1's high)
                        AND  C1.low <= C2.close <= C1.high  (body closed back in)
      entry = C2.close   stop = C2.high   target = min(C1.open, C1.close)  (C1 body low)
  Bullish CRT (LONG):   C2.low < C1.low
                        AND  C1.low <= C2.close <= C1.high
      entry = C2.close   stop = C2.low    target = max(C1.open, C1.close)  (C1 body high)

Win = target reached before the C2 extreme (the "protected" line) is touched.
Loss = C2 extreme touched first.  Unresolved within MAX_HOLD bars = time-stop
at the last close (EXPIRED, kept in the average — mirrors the live tracker).

Layers (the taught rules), each an opt-in flag so we can measure its contribution:
  --trend      only WITH the trend (EMA20>EMA50 -> longs only; < -> shorts only)
  --keylevel   CRT must sit at a key level: C2 swept a recent KL_LOOKBACK-bar
               extreme (old-high/low liquidity), not just C1
  --confirm    C3 entry: wait for the next candle to body-close in the trade
               direction beyond C2's body (single-candle distribution confirm),
               enter at C3 close instead of C2 close

Validation flags:
  --split=first / --split=last   walk-forward: run only the first/last half of
                                 each coin's history (early vs unseen regime)
  --long-only                    skip shorts (matches spot-only trading)
  --tf=4h,12h,1d   --history=N   --top=N   --jobs=N   --refresh

Timeframes: Daily (1d) + Weekly (1w) only — the exact charts the CRT series
tells beginners to use. (H4/H1 are the paired *entry* TFs for a later multi-TF
rung; 12h/30m are not part of the strategy and are excluded.)

Run:  python crt_backtest.py --trend --keylevel --history=4000 --top=40 --jobs=4
"""

import bisect
import json
import os
import sys

import pandas as pd

import agent
import universe
from data_source import make_exchange

EXCHANGE = make_exchange()
CACHE_DIR = "data/bt_cache"
REFRESH = "--refresh" in sys.argv

FEE = 0.001          # 0.1% per side -> 0.2% round-trip, same as backtest.py
for _a in sys.argv:                      # --fee=0.0015 -> test higher slippage/costs
    if _a.startswith("--fee="):
        FEE = float(_a.split("=", 1)[1])
MAX_HOLD = 200       # bars to give a trade before the time-stop closes it
KL_LOOKBACK = 20     # bars back a sweep must exceed to count as an "old high/low"
FVG_LOOKBACK = 15    # bars back to search for a Fair Value Gap key level
REJ_LOOKBACK = 20    # bars back to search for a Rejection Block (failed CISD)
REJ_TOL = 0.01       # how close the CRT must form to the rejection level (frac)

# --kl=oldhl,fvg,rejblock : which key-level TYPES count when --keylevel is on.
# The taught key levels are all three (FVG, Old High/Low, Rejection Block); a
# setup qualifies if it sits at ANY enabled one. Default = all three. Restrict
# to a single type to A/B which key level actually helps (esp. for shorts).
KL_TYPES = {"oldhl", "fvg", "rejblock"}
for _a in sys.argv:
    if _a.startswith("--kl="):
        KL_TYPES = set(_a.split("=", 1)[1].split(","))

# ---- flags ----
USE_TREND = "--trend" in sys.argv
USE_KEYLEVEL = "--keylevel" in sys.argv
USE_CONFIRM = "--confirm" in sys.argv
# --ote-entry : the TAUGHT candle-3 entry. Instead of chasing the C3 close, wait
# for candle 3 (bar i+1) to RETRACE into the OTE discount/premium zone of the C2
# range (0.705-0.786 fib) and enter there, with a TIGHT stop at C2's swept
# extreme (the turtle-soup wick). This is the fix for the wide-stop bleed: it
# shrinks risk to a fraction of the C2 range while keeping the same 50%/opposite
# targets. Fills only if candle 3 actually trades into the zone.
OTE_ENTRY = "--ote-entry" in sys.argv
OTE_FIB = 0.75       # centre of the 0.705-0.786 discount/premium retracement
for _a in sys.argv:
    if _a.startswith("--ote-fib="):
        OTE_FIB = float(_a.split("=", 1)[1])
LONG_ONLY = "--long-only" in sys.argv
SHORT_ONLY = "--short-only" in sys.argv
# --smt : Smart Money Technique (cross-asset divergence). Compare the coin's CRT
# sweep to a REFERENCE asset (BTC for alts, ETH for BTC): only take the CRT when
# the reference did NOT sweep the same way (divergence = SMT confirmation). The
# taught idea: if both sweep, the break is "real"; if only one sweeps, it's a
# fakeout that reverses.
SMT = "--smt" in sys.argv
# --inside-bar : the InsideBar CRT variant (flagged "high-probability" by the
# sources). Instead of C2 = the candle right after C1, require >=1 INSIDE bar(s)
# contained within C1's range (a consolidation), THEN a candle sweeps C1's
# extreme + closes back in. C1 = the "mother" candle of the consolidation.
INSIDE_BAR = "--inside-bar" in sys.argv
INSIDE_MAX = 5        # how many bars back to look for the inside-bar mother
# --- OTE-recipe levers (to test whether the recipe that rescued OTE also
#  rescues CRT): --partial banks 50% at TP1_R and moves the runner to break-even
#  (the group's risk mgmt); --target-r=N replaces the near C1-body target with a
#  FAR N-risk target (the group's ride-to-liquidity, R:R 4-5). Default off = the
#  faithful textbook CRT (single C1-body target, no partial).
USE_PARTIAL = "--partial" in sys.argv
TP1_R = 2.0
TARGET_R = None
# --target-mode : the CRT TARGET FRAMEWORK to test (from the new-source research).
#   c1body = our current target = C1's body (baseline)
#   mid    = single target = 50% of the CRT range (C1 high/low midpoint)
#   midopp = the taught framework: bank 50% at the 50% midpoint (+ move to BE),
#            then run the rest to the OPPOSITE extreme of the CRT range
TARGET_MODE = "c1body"
for _a in sys.argv:
    if _a.startswith("--tp1r="):
        TP1_R = float(_a.split("=", 1)[1])
    if _a.startswith("--target-r="):
        TARGET_R = float(_a.split("=", 1)[1])
    if _a.startswith("--target-mode="):
        TARGET_MODE = _a.split("=", 1)[1]
# --align : the taught timeframe-alignment model. Form the CRT (+ trend +
# key level) on the HIGHER timeframe, then drop to the aligned LOWER timeframe
# for the entry (a liquidity sweep + single-candle CISD), with a TIGHT stop at
# the swept LTF extreme and the target still at the HTF C1 body. Taught pairs:
# Weekly->H4 and Daily->H1.
ALIGN = "--align" in sys.argv
ALIGN_PAIRS = [("1w", "4h"), ("1d", "1h")]
ALIGN_WINDOW = 8      # HTF bars after C2 to keep hunting for the LTF entry
KL_LTF = 20           # LTF lookback for the entry liquidity sweep
SPLIT = None
# CRT-formation timeframes exactly as taught: beginners use Daily and Weekly
# (the series' recommended charts). H4/H1 are the *entry* timeframes in the
# taught pairs (Weekly->H4, Daily->H1); a later multi-TF rung can add those.
# 12h/30m are NOT part of the strategy and are deliberately excluded.
TIMEFRAMES = ["1d", "1w"]
HISTORY = 4000
TOP_N = 40
JOBS = 1
for _a in sys.argv:
    if _a.startswith("--split="):
        SPLIT = _a.split("=", 1)[1]
    if _a.startswith("--tf="):
        TIMEFRAMES = _a.split("=", 1)[1].split(",")
    if _a.startswith("--history="):
        HISTORY = int(_a.split("=", 1)[1])
    if _a.startswith("--top="):
        TOP_N = int(_a.split("=", 1)[1])
    if _a.startswith("--jobs="):
        JOBS = int(_a.split("=", 1)[1])


def get_history(coin, timeframe):
    """Same cache format/location as backtest.py, so candles are shared."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{coin.replace('/', '_')}_{timeframe}.json")
    if not REFRESH and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    if HISTORY <= 1000:
        bars = EXCHANGE.fetch_ohlcv(coin, timeframe, limit=HISTORY)
    else:
        tf_ms = EXCHANGE.parse_timeframe(timeframe) * 1000
        # Clamp to >=0: for weekly, HISTORY*tf_ms can predate the Unix epoch,
        # producing a negative startTime that Binance rejects. Fetching from 0
        # just returns from the coin's earliest listing, which is what we want.
        since = max(0, EXCHANGE.milliseconds() - HISTORY * tf_ms)
        bars = []
        while len(bars) < HISTORY:
            batch = EXCHANGE.fetch_ohlcv(coin, timeframe, since=since, limit=1000)
            if not batch:
                break
            bars += batch
            since = batch[-1][0] + tf_ms
            if len(batch) < 1000:
                break
        seen, uniq = set(), []
        for b in bars:
            if b[0] not in seen:
                seen.add(b[0]); uniq.append(b)
        bars = uniq
    with open(path, "w") as f:
        json.dump(bars, f)
    return bars


def _trend_dir(df, i):
    """Same EMA backbone the live bot uses as its regime signal."""
    e20, e50 = df["EMA20"].iat[i], df["EMA50"].iat[i]
    if pd.isna(e20) or pd.isna(e50):
        return None
    return "LONG" if e20 > e50 else "SHORT"


def _simulate(highs, lows, closes, i, direction, entry, stop, target):
    """Walk forward from bar i+1. Returns (result, pnl_pct, risk_pct).
    result in {WIN, LOSS, EXPIRED}. pnl is after round-trip fees."""
    risk_pct = abs(entry - stop) / entry * 100.0
    end = min(len(highs), i + 1 + MAX_HOLD)
    for k in range(i + 1, end):
        hi, lo = highs[k], lows[k]
        if direction == "LONG":
            if lo <= stop:                       # protected line touched -> loss
                gross = (stop - entry) / entry * 100.0
                return "LOSS", gross - FEE * 200, risk_pct
            if hi >= target:                     # C1 body reached -> win
                gross = (target - entry) / entry * 100.0
                return "WIN", gross - FEE * 200, risk_pct
        else:
            if hi >= stop:
                gross = (entry - stop) / entry * 100.0
                return "LOSS", gross - FEE * 200, risk_pct
            if lo <= target:
                gross = (entry - target) / entry * 100.0
                return "WIN", gross - FEE * 200, risk_pct
    # Time-stop: close at the last available close, keep it in the average.
    last = closes[end - 1]
    gross = ((last - entry) if direction == "LONG" else (entry - last)) / entry * 100.0
    return "EXPIRED", gross - FEE * 200, risk_pct


def _simulate_partial(highs, lows, closes, i, direction, entry, stop, target):
    """The group's risk management: bank 50% at TP1 = TP1_R risk multiples, move
    the runner's stop to break-even, run the rest to `target`. Same fee model
    and MAX_HOLD as _simulate. Returns (result, pnl_pct, risk_pct)."""
    risk = abs(entry - stop)
    risk_pct = risk / entry * 100.0
    tp1 = entry + TP1_R * risk if direction == "LONG" else entry - TP1_R * risk
    end = min(len(highs), i + 1 + MAX_HOLD)

    def pct(px):
        return ((px - entry) if direction == "LONG" else (entry - px)) / entry * 100.0

    banked = None
    for k in range(i + 1, end):
        hi, lo = highs[k], lows[k]
        if banked is None:
            hit_stop = (lo <= stop) if direction == "LONG" else (hi >= stop)
            hit_tp1 = (hi >= tp1) if direction == "LONG" else (lo <= tp1)
            if hit_stop:                       # stopped before TP1 -> full -1R
                return "LOSS", pct(stop) - FEE * 200, risk_pct
            if hit_tp1:
                banked = 0.5 * pct(tp1)        # 50% booked; runner stop -> BE
        else:
            hit_be = (lo <= entry) if direction == "LONG" else (hi >= entry)
            hit_tgt = (hi >= target) if direction == "LONG" else (lo <= target)
            if hit_be and not hit_tgt:
                return "WIN", banked - FEE * 200, risk_pct   # runner scratched at BE
            if hit_tgt:
                return "WIN", banked + 0.5 * pct(target) - FEE * 200, risk_pct
    if banked is None:
        last = closes[end - 1]
        return "EXPIRED", pct(last) - FEE * 200, risk_pct
    return "WIN", banked + 0.5 * pct(closes[end - 1]) - FEE * 200, risk_pct


_REF_CACHE = {}


def _get_ref(ref_coin, tf):
    """Reference-asset (timestamp->idx map, low[], high[]) for the SMT check,
    memoized. Built on the FULL reference history so lookups work regardless of
    the coin's split window."""
    key = (ref_coin, tf)
    if key not in _REF_CACHE:
        bars = get_history(ref_coin, tf)
        rlo = [b[3] for b in bars]          # OHLCV: [ts,open,high,low,close,vol]
        rhi = [b[2] for b in bars]
        rts = {int(b[0]): idx for idx, b in enumerate(bars)}
        _REF_CACHE[key] = (rts, rlo, rhi)
    return _REF_CACHE[key]


def _smt_confirms(coin, tf, ts_i, direction):
    """True if the reference asset does NOT sweep the same way at this bar
    (a Smart-Money-Technique divergence in the CRT's favour)."""
    ref_coin = "ETH/USDT" if coin == "BTC/USDT" else "BTC/USDT"
    rts, rlo, rhi = _get_ref(ref_coin, tf)
    j = rts.get(int(ts_i))
    if j is None or j < 1:
        return False                        # no aligned ref bar -> can't confirm
    if direction == "LONG":
        return not (rlo[j] < rlo[j - 1])    # coin swept its low; ref did NOT = SMT
    return not (rhi[j] > rhi[j - 1])         # coin swept its high; ref did NOT = SMT


def _simulate_partial_explicit(highs, lows, closes, i, direction, entry, stop, tp1, tp2):
    """Bank 50% at tp1 (move the runner's stop to break-even), run the rest to
    tp2. Explicit PRICE targets (used by the 50%+opposite-extreme framework).
    Same fee model and MAX_HOLD as the other sims."""
    risk = abs(entry - stop)
    risk_pct = risk / entry * 100.0
    end = min(len(highs), i + 1 + MAX_HOLD)

    def pct(px):
        return ((px - entry) if direction == "LONG" else (entry - px)) / entry * 100.0

    banked = None
    for k in range(i + 1, end):
        hi, loo = highs[k], lows[k]
        if banked is None:
            hit_stop = (loo <= stop) if direction == "LONG" else (hi >= stop)
            hit_tp1 = (hi >= tp1) if direction == "LONG" else (loo <= tp1)
            if hit_stop:
                return "LOSS", pct(stop) - FEE * 200, risk_pct
            if hit_tp1:
                banked = 0.5 * pct(tp1)
        else:
            hit_be = (loo <= entry) if direction == "LONG" else (hi >= entry)
            hit_tgt = (hi >= tp2) if direction == "LONG" else (loo <= tp2)
            if hit_be and not hit_tgt:
                return "WIN", banked - FEE * 200, risk_pct
            if hit_tgt:
                return "WIN", banked + 0.5 * pct(tp2) - FEE * 200, risk_pct
    if banked is None:
        return "EXPIRED", pct(closes[end - 1]) - FEE * 200, risk_pct
    return "WIN", banked + 0.5 * pct(closes[end - 1]) - FEE * 200, risk_pct


def _run_sim(highs, lows, closes, i, direction, entry, stop, target):
    """Route a trade through the chosen simulator, optionally overriding the
    near C1-body target with a FAR N-risk target (--target-r) and/or using the
    2R-partial+BE plan (--partial)."""
    if TARGET_R is not None:
        risk = abs(entry - stop)
        target = entry + TARGET_R * risk if direction == "LONG" else entry - TARGET_R * risk
    if USE_PARTIAL:
        return _simulate_partial(highs, lows, closes, i, direction, entry, stop, target)
    return _simulate(highs, lows, closes, i, direction, entry, stop, target)


def _has_fvg(o, h, l, c, i, direction):
    """True if the CRT's swept extreme taps a matching-direction 3-candle FVG
    (imbalance) in the recent lookback — bullish gap high[a]<low[k] (demand)
    for longs, bearish gap low[a]>high[k] (supply) for shorts."""
    swept = l[i] if direction == "LONG" else h[i]
    start = max(2, i - FVG_LOOKBACK)
    for k in range(i, start - 1, -1):
        a = k - 2
        if a < 0:
            break
        if direction == "LONG" and h[a] < l[k]:      # bullish FVG zone [h[a], l[k]]
            if h[a] <= swept <= l[k]:
                return True
        if direction == "SHORT" and l[a] > h[k]:     # bearish FVG zone [h[k], l[a]]
            if h[k] <= swept <= l[a]:
                return True
    return False


def _has_rejblock(o, h, l, c, i, direction):
    """Rejection Block = a failed CISD (approximation): a recent candle swept a
    prior KL_LOOKBACK extreme with its WICK but CLOSED back inside (the breakout
    failed and held), and the current CRT is forming at that same level. A held
    failed-breakdown = bullish rejection block (support); failed-breakout =
    bearish rejection block (resistance)."""
    swept = l[i] if direction == "LONG" else h[i]
    start = max(KL_LOOKBACK + 1, i - REJ_LOOKBACK)
    for j in range(i - 1, start - 1, -1):
        if direction == "LONG":
            prior_lo = l[j - KL_LOOKBACK:j].min()
            if l[j] < prior_lo <= c[j] and abs(swept - l[j]) / l[j] <= REJ_TOL:
                return True
        else:
            prior_hi = h[j - KL_LOOKBACK:j].max()
            if h[j] > prior_hi >= c[j] and abs(swept - h[j]) / h[j] <= REJ_TOL:
                return True
    return False


def _at_keylevel(o, h, l, c, i, direction):
    """Setup qualifies if it sits at ANY enabled key-level type."""
    if "oldhl" in KL_TYPES:
        if direction == "SHORT" and h[i] >= h[i - KL_LOOKBACK:i].max():
            return True
        if direction == "LONG" and l[i] <= l[i - KL_LOOKBACK:i].min():
            return True
    if "fvg" in KL_TYPES and _has_fvg(o, h, l, c, i, direction):
        return True
    if "rejblock" in KL_TYPES and _has_rejblock(o, h, l, c, i, direction):
        return True
    return False


def backtest_coin(coin):
    """Return a stats dict for one coin across all timeframes."""
    st = {"wins": 0, "losses": 0, "expired": 0, "pnl": 0.0,
          "win_pnl": 0.0, "loss_pnl": 0.0, "r_sum": 0.0, "n": 0,
          "bh_pnl": 0.0, "bh_n": 0}
    for tf in TIMEFRAMES:
        bars = get_history(coin, tf)
        if len(bars) > HISTORY:
            bars = bars[-HISTORY:]
        if SPLIT == "first":
            bars = bars[: len(bars) // 2]
        elif SPLIT == "last":
            bars = bars[len(bars) // 2:]
        if len(bars) < 120:
            continue

        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        agent.add_indicators(df)
        o = df["open"].to_numpy(); h = df["high"].to_numpy()
        lo = df["low"].to_numpy(); c = df["close"].to_numpy()
        ts = df["timestamp"].to_numpy()

        # Buy-and-hold benchmark over this exact window (the crypto reality check).
        if len(c) > 1 and c[0] > 0:
            st["bh_pnl"] += (c[-1] - c[0]) / c[0] * 100.0
            st["bh_n"] += 1

        start = max(KL_LOOKBACK + 1, 55)   # need history for MAs + key-level lookback
        for i in range(start, len(df) - 1):
            if INSIDE_BAR:
                # find the earliest "mother" candle whose range contains every
                # candle between it and i-1 (>=1 inside bar) — a consolidation.
                mother = None
                for m in range(i - 2, max(i - 2 - INSIDE_MAX, start) - 1, -1):
                    if all(h[k] <= h[m] and lo[k] >= lo[m] for k in range(m + 1, i)):
                        mother = m
                    else:
                        break
                if mother is None:
                    continue
                c1_hi, c1_lo, c1_o, c1_c = h[mother], lo[mother], o[mother], c[mother]
            else:
                c1_hi, c1_lo, c1_o, c1_c = h[i - 1], lo[i - 1], o[i - 1], c[i - 1]
            c2_hi, c2_lo, c2_close = h[i], lo[i], c[i]

            swept_high = c2_hi > c1_hi
            swept_low = c2_lo < c1_lo
            closed_in = c1_lo <= c2_close <= c1_hi
            if not closed_in:
                continue
            if swept_high == swept_low:        # neither, or ambiguous outside-bar
                continue

            if swept_high:
                direction = "SHORT"
                entry, stop = c2_close, c2_hi
                target = min(c1_o, c1_c)        # C1 body low
                if target >= entry:             # no room left to the target
                    continue
            else:
                direction = "LONG"
                entry, stop = c2_close, c2_lo
                target = max(c1_o, c1_c)        # C1 body high
                if target <= entry:
                    continue

            if LONG_ONLY and direction == "SHORT":
                continue
            if SHORT_ONLY and direction == "LONG":
                continue

            # --- Layer: with-trend only ---
            if USE_TREND:
                td = _trend_dir(df, i)
                if td is None or td != direction:
                    continue

            # --- Layer: key level (FVG / old high-low / rejection block) ---
            if USE_KEYLEVEL and not _at_keylevel(o, h, lo, c, i, direction):
                continue

            # --- Layer: SMT (cross-asset divergence confirmation) ---
            if SMT and not _smt_confirms(coin, tf, ts[i], direction):
                continue

            enter_i = i
            # --- Taught candle-3 OTE entry: retrace into the discount/premium
            #     zone of the C2 range, tight stop at the swept extreme ---
            if OTE_ENTRY:
                j = i + 1
                if j >= len(df):
                    continue
                rng = c2_hi - c2_lo
                if rng <= 0:
                    continue
                if direction == "LONG":
                    entry = c2_hi - OTE_FIB * rng     # discount zone entry
                    stop = c2_lo                       # tight: the swept low
                    if not (lo[j] <= entry):           # candle 3 must reach it
                        continue
                    if entry <= stop:
                        continue
                else:
                    entry = c2_lo + OTE_FIB * rng     # premium zone entry
                    stop = c2_hi                       # tight: the swept high
                    if not (h[j] >= entry):
                        continue
                    if entry >= stop:
                        continue
                enter_i = i   # sim from candle 3 (i+1) onward; it is the fill bar
            # --- Layer: C3 confirmation (single-candle distribution close) ---
            elif USE_CONFIRM:
                j = i + 1
                if j >= len(df):
                    continue
                c2_body_lo, c2_body_hi = min(o[i], c[i]), max(o[i], c[i])
                if direction == "SHORT" and not (c[j] < c2_body_lo):
                    continue
                if direction == "LONG" and not (c[j] > c2_body_hi):
                    continue
                entry = c[j]                    # enter on C3 close
                # target/stop unchanged; re-check room after the shifted entry
                if direction == "SHORT" and target >= entry:
                    continue
                if direction == "LONG" and target <= entry:
                    continue
                enter_i = j

            # --- CRT target framework (baseline C1-body, or 50%/opposite-extreme) ---
            if TARGET_MODE == "c1body":
                result, pnl, risk = _run_sim(h, lo, c, enter_i, direction, entry, stop, target)
            else:
                mid = (c1_hi + c1_lo) / 2.0                 # 50% of the CRT range
                opp = c1_hi if direction == "LONG" else c1_lo   # opposite extreme
                mid_beyond = (mid > entry) if direction == "LONG" else (mid < entry)
                if TARGET_MODE == "mid":
                    if not mid_beyond:
                        continue                            # 50% already reached at entry
                    result, pnl, risk = _simulate(h, lo, c, enter_i, direction, entry, stop, mid)
                elif TARGET_MODE == "midopp":
                    if mid_beyond:
                        result, pnl, risk = _simulate_partial_explicit(
                            h, lo, c, enter_i, direction, entry, stop, mid, opp)
                    else:                                   # past 50% -> just run to the opposite extreme
                        result, pnl, risk = _simulate(h, lo, c, enter_i, direction, entry, stop, opp)
                else:
                    result, pnl, risk = _run_sim(h, lo, c, enter_i, direction, entry, stop, target)
            st["n"] += 1
            st["pnl"] += pnl
            st["r_sum"] += pnl / risk if risk else 0.0
            if result == "WIN":
                st["wins"] += 1; st["win_pnl"] += pnl
            elif result == "LOSS":
                st["losses"] += 1; st["loss_pnl"] += pnl
            else:
                st["expired"] += 1
                # count expired toward win/loss by sign for the win-rate view
                if pnl > 0:
                    st["win_pnl"] += pnl
                else:
                    st["loss_pnl"] += pnl
    return st


def _ltf_entry(o, h, l, c, ts, start_idx, end_ts, direction):
    """Scan LTF bars from start_idx while ts < end_ts for the taught C3 entry:
    a liquidity sweep of a recent LTF extreme + a single-candle CISD close in
    the trade direction. Returns (enter_idx, entry, stop) or None."""
    n = len(c)
    j = max(start_idx, KL_LTF)
    while j < n - 1 and ts[j] < end_ts:
        if direction == "LONG":
            if l[j] < l[j - KL_LTF:j].min() and c[j + 1] > max(o[j], c[j]):
                return j + 1, c[j + 1], l[j]        # stop = the swept LTF low
        else:
            if h[j] > h[j - KL_LTF:j].max() and c[j + 1] < min(o[j], c[j]):
                return j + 1, c[j + 1], h[j]        # stop = the swept LTF high
        j += 1
    return None


def backtest_coin_aligned(coin):
    """Timeframe-alignment CRT: HTF forms the setup/bias/target, LTF gives the
    entry + tight stop. Iterates the taught pairs (Weekly->H4, Daily->H1)."""
    st = {"wins": 0, "losses": 0, "expired": 0, "pnl": 0.0,
          "win_pnl": 0.0, "loss_pnl": 0.0, "r_sum": 0.0, "n": 0,
          "bh_pnl": 0.0, "bh_n": 0}
    pairs = [(htf, ltf) for (htf, ltf) in ALIGN_PAIRS if htf in TIMEFRAMES]
    for htf, ltf in pairs:
        hbars = get_history(coin, htf)
        if len(hbars) > HISTORY:
            hbars = hbars[-HISTORY:]
        if SPLIT == "first":
            hbars = hbars[: len(hbars) // 2]
        elif SPLIT == "last":
            hbars = hbars[len(hbars) // 2:]
        lbars = get_history(coin, ltf)
        if len(hbars) < 120 or len(lbars) < 120:
            continue

        hdf = pd.DataFrame(hbars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        agent.add_indicators(hdf)
        ho = hdf["open"].to_numpy(); hh = hdf["high"].to_numpy()
        hl = hdf["low"].to_numpy(); hc = hdf["close"].to_numpy()
        hts = hdf["timestamp"].to_numpy()
        htf_ms = EXCHANGE.parse_timeframe(htf) * 1000

        ldf = pd.DataFrame(lbars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        lo_ = ldf["open"].to_numpy(); lh = ldf["high"].to_numpy()
        ll = ldf["low"].to_numpy(); lc = ldf["close"].to_numpy()
        lts = ldf["timestamp"].to_numpy().tolist()

        if hc[-1] > 0 and hc[0] > 0:               # buy-hold benchmark on HTF
            st["bh_pnl"] += (hc[-1] - hc[0]) / hc[0] * 100.0
            st["bh_n"] += 1

        start = max(KL_LOOKBACK + 1, 55)
        for i in range(start, len(hdf) - 1):
            c1_hi, c1_lo, c1_o, c1_c = hh[i - 1], hl[i - 1], ho[i - 1], hc[i - 1]
            swept_high = hh[i] > c1_hi
            swept_low = hl[i] < c1_lo
            if not (c1_lo <= hc[i] <= c1_hi):
                continue
            if swept_high == swept_low:
                continue
            direction = "SHORT" if swept_high else "LONG"
            target = min(c1_o, c1_c) if swept_high else max(c1_o, c1_c)

            if LONG_ONLY and direction == "SHORT":
                continue
            if SHORT_ONLY and direction == "LONG":
                continue
            if USE_TREND:
                td = _trend_dir(hdf, i)
                if td is None or td != direction:
                    continue
            if USE_KEYLEVEL and not _at_keylevel(ho, hh, hl, hc, i, direction):
                continue
            # --- SMT (cross-asset divergence) on the HTF setup ---
            if SMT and not _smt_confirms(coin, htf, hts[i], direction):
                continue

            t2 = int(hts[i]) + htf_ms                 # C2 close time
            if not lts or t2 < lts[0] or t2 > lts[-1]:
                continue                              # no LTF coverage here
            start_idx = bisect.bisect_left(lts, t2)
            end_ts = t2 + ALIGN_WINDOW * htf_ms
            res = _ltf_entry(lo_, lh, ll, lc, lts, start_idx, end_ts, direction)
            if res is None:
                continue
            enter_idx, entry, stop = res
            # --- target framework (HTF C1 body, or 50%/opposite-extreme) ---
            if TARGET_MODE == "c1body":
                if (direction == "LONG" and target <= entry) or \
                   (direction == "SHORT" and target >= entry):
                    continue
                result, pnl, risk = _run_sim(lh, ll, lc, enter_idx, direction, entry, stop, target)
            else:
                mid = (c1_hi + c1_lo) / 2.0            # 50% of the HTF CRT range
                opp = c1_hi if direction == "LONG" else c1_lo
                mid_beyond = (mid > entry) if direction == "LONG" else (mid < entry)
                if TARGET_MODE == "mid":
                    if not mid_beyond:
                        continue
                    result, pnl, risk = _simulate(lh, ll, lc, enter_idx, direction, entry, stop, mid)
                elif TARGET_MODE == "midopp":
                    if mid_beyond:
                        result, pnl, risk = _simulate_partial_explicit(
                            lh, ll, lc, enter_idx, direction, entry, stop, mid, opp)
                    else:
                        result, pnl, risk = _simulate(lh, ll, lc, enter_idx, direction, entry, stop, opp)
                else:
                    result, pnl, risk = _run_sim(lh, ll, lc, enter_idx, direction, entry, stop, target)
            st["n"] += 1
            st["pnl"] += pnl
            st["r_sum"] += pnl / risk if risk else 0.0
            if result == "WIN":
                st["wins"] += 1; st["win_pnl"] += pnl
            elif result == "LOSS":
                st["losses"] += 1; st["loss_pnl"] += pnl
            else:
                st["expired"] += 1
                if pnl > 0:
                    st["win_pnl"] += pnl
                else:
                    st["loss_pnl"] += pnl
    return st


def main():
    coins_file = None
    for _a in sys.argv:
        if _a.startswith("--coins-file="):
            coins_file = _a.split("=", 1)[1]
    if coins_file and os.path.exists(coins_file):
        with open(coins_file) as f:                      # PINNED universe = clean, reproducible
            coins = [ln.strip() for ln in f if ln.strip()][:TOP_N]
    else:
        try:
            coins = universe.get_universe(EXCHANGE, 100)[:TOP_N]
        except Exception as e:
            print(f"universe unavailable ({type(e).__name__}); using majors fallback")
            coins = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
                     "AVAX/USDT", "LINK/USDT", "LTC/USDT", "DOT/USDT", "DOGE/USDT"]

    layers = []
    if USE_TREND: layers.append("trend")
    if USE_KEYLEVEL: layers.append(f"keylevel[{'+'.join(sorted(KL_TYPES))}]")
    if USE_CONFIRM: layers.append("C3-confirm")
    if OTE_ENTRY: layers.append(f"ote-entry[{OTE_FIB}]")
    if LONG_ONLY: layers.append("long-only")
    if SHORT_ONLY: layers.append("short-only")
    layers.append(f"target={TARGET_MODE}")
    if SMT: layers.append("SMT")
    if INSIDE_BAR: layers.append("inside-bar")
    coin_fn = backtest_coin_aligned if ALIGN else backtest_coin
    mode = f"ALIGNED {[f'{h}->{l}' for h, l in ALIGN_PAIRS if h in TIMEFRAMES]}" if ALIGN else f"single-TF {TIMEFRAMES}"
    print(f"CRT backtest | coins={len(coins)} | mode={mode} | history={HISTORY}"
          f" | split={SPLIT or 'full'} | layers={layers or ['BASE']}")
    print(f"Fee modelled: {FEE*200:.1f}% round-trip\n")

    total = {"wins": 0, "losses": 0, "expired": 0, "pnl": 0.0,
             "win_pnl": 0.0, "loss_pnl": 0.0, "r_sum": 0.0, "n": 0,
             "bh_pnl": 0.0, "bh_n": 0}

    def merge(dst, src):
        for k in dst:
            dst[k] += src[k]

    if JOBS > 1 and len(coins) > 1:
        import multiprocessing as mp
        with mp.Pool(min(JOBS, len(coins))) as pool:
            for st in pool.imap_unordered(coin_fn, coins):
                merge(total, st)
    else:
        for coin in coins:
            try:
                merge(total, coin_fn(coin))
            except Exception as e:
                print(f"  skip {coin}: {type(e).__name__}: {e}")

    n = total["n"]
    resolved = total["wins"] + total["losses"]
    print("========== CRT RESULTS ==========")
    if n == 0:
        print("No CRT setups found.")
        print("=================================")
        return
    win_rate = total["wins"] / resolved * 100 if resolved else 0
    avg = total["pnl"] / n
    avg_win = total["win_pnl"] / total["wins"] if total["wins"] else 0
    n_loss = total["losses"] + max(0, total["expired"])  # rough denom for avg loss
    avg_loss = total["loss_pnl"] / total["losses"] if total["losses"] else 0
    avg_r = total["r_sum"] / n
    bh = total["bh_pnl"] / total["bh_n"] if total["bh_n"] else 0
    print(f"Trades (incl. expired): {n}")
    print(f"  wins={total['wins']}  losses={total['losses']}  expired={total['expired']}")
    print(f"Win rate (resolved)   : {win_rate:.1f}%")
    print(f"Avg P&L / trade        : {avg:+.2f}%   (after fees)")
    print(f"Total P&L              : {total['pnl']:+.1f}%")
    print(f"Avg winner / loser     : +{avg_win:.2f}% / {avg_loss:.2f}%")
    print(f"Avg R multiple / trade : {avg_r:+.2f}R")
    print(f"Buy-and-hold benchmark : {bh:+.1f}% avg per coin over the window")
    print("=================================")


if __name__ == "__main__":
    main()
