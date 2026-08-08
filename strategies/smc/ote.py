"""OTE / "Textbook Setup" — the ICT-2022 model, built SEPARATE from CRT.

This is the second codeable strategy the mentor group uses (distinct from Candle
Range Theory). Its sequence, reduced to strict mechanical rules:

    1. Liquidity sweep : price grabs a prior swing low (sellside) for a long, or a
                         prior swing high (buyside) for a short.  Call the swept
                         extreme the ORIGIN (L for longs, H for shorts).
    2. Displacement    : from the origin, price moves impulsively and leaves a
       + MSS             Fair Value Gap (imbalance = the displacement footprint),
                         then CLOSES beyond the opposite short-term pivot = a
                         Market Structure Shift (the turn is confirmed).
    3. OTE retrace     : price pulls back into the Optimal Trade Entry band =
                         0.705-0.786 retracement of the impulse leg (origin->
                         extreme).  A limit sits in that discount/premium zone.
    4. Entry / Stop    : fill on the retrace into the OTE band; stop just beyond
                         the origin (the swept extreme).
    5. Target          : the NEXT liquidity in the trade direction = the nearest
                         resting swing high (long) / low (short) beyond the
                         impulse extreme; fallback = a 1:1 leg projection.

Everything here is a pure, no-lookahead function of OHLC.  `detect_ote(df)`
evaluates the last CLOSED bar for live use; the numpy core (`scan_ote`) is what
the backtester calls at every bar.  No network, no side effects, no DB.

Faithful, mechanical, and deliberately rare — like the real setup.
"""

import bisect

import numpy as np


# ---- tunables (kept module-level so the backtester can override) ----
SWING_LB = 2        # bars each side for a confirmed swing pivot
IMPULSE_LB = 30     # how far back the impulse leg / sweep may reach from the MSS bar
TARGET_LB = 60      # how far back to hunt for the next resting-liquidity target
OTE_LOW = 0.705     # top of the OTE band (shallowest fill)
OTE_HIGH = 0.786    # bottom of the OTE band (deepest fill)
OTE_FIB = 0.75      # the limit level inside the band we actually rest the order at
STOP_BUF = 0.001    # push the stop this fraction beyond the origin (breathing room)
MIN_RR = 1.5        # discard setups whose target/stop reward:risk is below this
ENTRY_WINDOW = 20   # bars after the MSS the OTE limit may still fill (live + backtest)


def _confirmed_swings(h, l, lb=SWING_LB):
    """Return (hi_idx, hi_px, lo_idx, lo_px) as index-sorted parallel lists.

    A swing at index j needs `lb` bars on BOTH sides, so it is only knowable at
    bar j+lb.  Callers must therefore only use swings with idx <= i-lb to stay
    strictly no-lookahead.  Parallel index-sorted lists let callers slice a
    recent window with bisect instead of rescanning every swing at every bar.
    """
    hi_idx, hi_px, lo_idx, lo_px = [], [], [], []
    n = len(h)
    for j in range(lb, n - lb):
        if h[j] >= h[j - lb:j + lb + 1].max():
            hi_idx.append(j); hi_px.append(float(h[j]))
        if l[j] <= l[j - lb:j + lb + 1].min():
            lo_idx.append(j); lo_px.append(float(l[j]))
    return hi_idx, hi_px, lo_idx, lo_px


def _has_bull_fvg(h, l, a, b):
    """Any bullish 3-candle FVG (imbalance) inside [a, b]? high[k-2] < low[k]."""
    for k in range(a + 2, b + 1):
        if h[k - 2] < l[k]:
            return True
    return False


def _has_bear_fvg(h, l, a, b):
    """Any bearish 3-candle FVG inside [a, b]? low[k-2] > high[k]."""
    for k in range(a + 2, b + 1):
        if l[k - 2] > h[k]:
            return True
    return False


def detect_ote_at(o, h, l, c, i, swings, require_fvg=True):
    """Evaluate an OTE setup whose MSS/displacement completes AT bar i.

    `swings` = (hi_idx, hi_px, lo_idx, lo_px), the index-sorted confirmed swings
    for the whole series (from `_confirmed_swings`).  Returns a signal dict or
    None.  No lookahead: only bars <= i and swings confirmed by bar i are read.

    Signal dict:
      direction  "LONG" | "SHORT"
      origin     the swept extreme (L for long, H for short) = stop anchor
      extreme    the impulse extreme (H for long, L for short) = 100% fib
      ote_top/ote_bottom   the OTE price band
      entry      the resting limit price (OTE_FIB level)
      stop       just beyond the origin
      target     next resting liquidity (or 1:1 projection fallback)
      mss_level  the short-term pivot the close broke (for the alert text)
    """
    lb = SWING_LB
    hi_idx, hi_px, lo_idx, lo_px = swings
    hi_lo_bound = i - IMPULSE_LB
    conf = i - lb                                    # newest swing usable at bar i

    # Most recent confirmed swing high/low in [i-IMPULSE_LB, i-lb], via bisect.
    a = bisect.bisect_left(hi_idx, hi_lo_bound)
    b = bisect.bisect_right(hi_idx, conf)
    sth_i, sth_p = (hi_idx[b - 1], hi_px[b - 1]) if b > a else (None, None)
    a2 = bisect.bisect_left(lo_idx, hi_lo_bound)
    b2 = bisect.bisect_right(lo_idx, conf)
    stl_i, stl_p = (lo_idx[b2 - 1], lo_px[b2 - 1]) if b2 > a2 else (None, None)

    # ---------------- LONG ----------------
    long_sig = None
    if sth_p is not None and c[i] > sth_p and c[i - 1] <= sth_p:   # first MSS up close
        L_idx = int(np.argmin(l[sth_i:i + 1])) + sth_i   # impulse origin = lowest low
        L = float(l[L_idx])
        # sweep: the origin must have undercut a prior swing low (grabbed sellside)
        pa = bisect.bisect_left(lo_idx, i - IMPULSE_LB - TARGET_LB)
        pb = bisect.bisect_left(lo_idx, L_idx)
        swept = pb > pa and L < min(lo_px[pa:pb])
        H = float(h[L_idx:i + 1].max())
        disp = (not require_fvg) or _has_bull_fvg(h, l, L_idx, i)
        if swept and disp and H > L:
            leg = H - L
            entry = H - OTE_FIB * leg
            stop = L * (1 - STOP_BUF)
            # target = nearest resting swing high strictly above H (next buyside)
            ta = bisect.bisect_left(hi_idx, i - TARGET_LB)
            above = [hi_px[k] for k in range(ta, b) if hi_px[k] > H]
            target = min(above) if above else H + leg    # fallback: 1:1 projection
            if entry > stop and target > entry:
                risk = entry - stop
                if risk > 0 and (target - entry) / risk >= MIN_RR:
                    long_sig = {
                        "direction": "LONG", "origin": L, "extreme": H,
                        "ote_top": H - OTE_LOW * leg, "ote_bottom": H - OTE_HIGH * leg,
                        "entry": entry, "stop": stop, "target": target,
                        "mss_level": sth_p,
                    }

    # ---------------- SHORT ----------------
    short_sig = None
    if stl_p is not None and c[i] < stl_p and c[i - 1] >= stl_p:   # first MSS down close
        H_idx = int(np.argmax(h[stl_i:i + 1])) + stl_i    # impulse origin = highest high
        H = float(h[H_idx])
        pa = bisect.bisect_left(hi_idx, i - IMPULSE_LB - TARGET_LB)
        pb = bisect.bisect_left(hi_idx, H_idx)
        swept = pb > pa and H > max(hi_px[pa:pb])
        L = float(l[H_idx:i + 1].min())
        disp = (not require_fvg) or _has_bear_fvg(h, l, H_idx, i)
        if swept and disp and H > L:
            leg = H - L
            entry = L + OTE_FIB * leg
            stop = H * (1 + STOP_BUF)
            ta = bisect.bisect_left(lo_idx, i - TARGET_LB)
            below = [lo_px[k] for k in range(ta, b2) if lo_px[k] < L]
            target = max(below) if below else L - leg
            if entry < stop and target < entry:
                risk = stop - entry
                if risk > 0 and (entry - target) / risk >= MIN_RR:
                    short_sig = {
                        "direction": "SHORT", "origin": H, "extreme": L,
                        "ote_top": L + OTE_HIGH * leg, "ote_bottom": L + OTE_LOW * leg,
                        "entry": entry, "stop": stop, "target": target,
                        "mss_level": stl_p,
                    }

    # If both fire on the same bar (rare, choppy), skip — no clean bias.
    if long_sig and short_sig:
        return None
    return long_sig or short_sig


def scan_ote(o, h, l, c, require_fvg=True):
    """Yield (i, signal) for every bar whose OTE setup completes at i.
    Precomputes swings once; each bar is O(log S + window) via bisect."""
    swings = _confirmed_swings(h, l)
    n = len(c)
    start = SWING_LB + IMPULSE_LB
    for i in range(start, n):
        sig = detect_ote_at(o, h, l, c, i, swings, require_fvg=require_fvg)
        if sig is not None:
            yield i, sig


def detect_ote(df, require_fvg=True):
    """Setup-completion wrapper: does an OTE setup COMPLETE (MSS) on the LAST
    CLOSED bar? Entry is still pending its retrace fill. Used for research; the
    live bot uses `detect_ote_live` so the fill timing is realistic."""
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    if len(c) < SWING_LB + IMPULSE_LB + 2:
        return None
    swings = _confirmed_swings(h, l)
    i = len(c) - 1
    return detect_ote_at(o, h, l, c, i, swings, require_fvg=require_fvg)


def detect_ote_live(df, require_fvg=True, entry_window=None):
    """LIVE trigger. Fire ONLY when the OTE limit would fill on the LAST CLOSED
    bar of a valid recent setup — i.e. an MSS completed within `entry_window`
    bars, price has now retraced into the zone for the FIRST time, and nothing
    hit the stop or target on the way. This mirrors the backtester's stage-1
    fill, so the paper tracker opening at `entry` is realistic (not an assumed
    fill at a level price never reached). Returns the signal dict or None.
    """
    if entry_window is None:
        entry_window = ENTRY_WINDOW
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(c)
    if n < SWING_LB + IMPULSE_LB + 3:
        return None
    swings = _confirmed_swings(h, l)
    last = n - 1
    lo_bound = max(SWING_LB + IMPULSE_LB, last - entry_window)
    for m in range(last - 1, lo_bound - 1, -1):        # MSS-completion candidates
        sig = detect_ote_at(o, h, l, c, m, swings, require_fvg=require_fvg)
        if sig is None:
            continue
        d = sig["direction"]; entry = sig["entry"]; stop = sig["stop"]; target = sig["target"]
        # Between the MSS bar and the last bar: the limit must NOT have filled yet
        # and the setup must NOT have been invalidated (stop) or completed (target).
        spent = False
        for k in range(m + 1, last):
            if d == "LONG":
                if h[k] >= target or l[k] <= stop or l[k] <= entry:
                    spent = True; break
            else:
                if l[k] <= target or h[k] >= stop or h[k] >= entry:
                    spent = True; break
        if spent:
            continue
        # The last closed bar is the FIRST touch of the limit (fill), without the
        # same bar also gapping to the stop or the target.
        if d == "LONG":
            if l[last] <= entry and l[last] > stop and h[last] < target:
                return sig
        else:
            if h[last] >= entry and h[last] < stop and l[last] > target:
                return sig
    return None


def htf_bias(df):
    """Higher-timeframe market-structure bias for the alignment filter.
    UP = higher high AND higher low on the last two confirmed swings; DOWN =
    lower high AND lower low; else NEUTRAL. All bars closed, so no lookahead."""
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    hi_idx, hi_px, lo_idx, lo_px = _confirmed_swings(h, l)
    if len(hi_px) < 2 or len(lo_px) < 2:
        return "NEUTRAL"
    if hi_px[-1] > hi_px[-2] and lo_px[-1] > lo_px[-2]:
        return "UP"
    if hi_px[-1] < hi_px[-2] and lo_px[-1] < lo_px[-2]:
        return "DOWN"
    return "NEUTRAL"


if __name__ == "__main__":
    import ccxt
    import pandas as pd

    ex = ccxt.binanceus()
    for coin in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        bars = ex.fetch_ohlcv(coin, timeframe="4h", limit=500)
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        o = df["open"].to_numpy(); h = df["high"].to_numpy()
        l = df["low"].to_numpy(); c = df["close"].to_numpy()
        sigs = list(scan_ote(o, h, l, c))
        print(f"{coin}: {len(sigs)} OTE setups over {len(df)} bars (4h)")
        if sigs:
            i, s = sigs[-1]
            print(f"   last @bar {i}: {s['direction']} entry={s['entry']:.4f} "
                  f"stop={s['stop']:.4f} target={s['target']:.4f}")
