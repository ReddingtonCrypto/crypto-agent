"""CRT (Candle Range Theory) — HTF setup-marker for the live bot.

Alerts-only by design: the bot MARKS a valid Daily/Weekly CRT setup (the wide
C2 "protected" stop + the C1-body target) and the human applies the
discretionary lower-timeframe entry and final judgement. This is the honest way
to run a discretionary strategy — the bot finds the setup, you decide the trade.

Faithful to the taught rules for the HTF side:
  * C1 range -> C2 sweeps C1's high (bearish) or low (bullish) AND C2's body
    closes back INSIDE C1's range.
  * With-trend only (market structure: HH+HL = up, LH+LL = down).
  * At a key level: C2 swept a recent extreme (old high/low) OR an FVG sits at
    the setup.
  * Stop = C2's swept extreme (the protected line). Target = C1's BODY.

detect_crt(df) takes CLOSED candles and reads the last one as C2, the prior as
C1. Returns {direction, stop, target, key_level} or None.
"""

from strategies.smc.market_structure import find_swings
from strategies.smc import key_levels

KL_LOOKBACK = 20   # bars back a sweep must exceed to count as an "old high/low"


def _trend(highs, lows):
    """Market-structure trend from the last two confirmed swings of each."""
    if len(highs) < 2 or len(lows) < 2:
        return None
    if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]:
        return "LONG"
    if highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]:
        return "SHORT"
    return None


def _fvg_near(df, i, direction, look=5):
    """A matching-direction 3-candle FVG in the recent lookback."""
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    for k in range(i, max(2, i - look) - 1, -1):
        a = k - 2
        if a < 0:
            break
        if direction == "LONG" and h[a] < l[k]:
            return True
        if direction == "SHORT" and l[a] > h[k]:
            return True
    return False


def detect_crt(df, kl_lookback=KL_LOOKBACK):
    if len(df) < max(kl_lookback + 3, 30):
        return None

    c1 = df.iloc[-2]
    c2 = df.iloc[-1]
    if c2.high > c1.high and c1.low <= c2.close <= c1.high:
        direction = "SHORT"
        stop = float(c2.high)
        target = float(min(c1.open, c1.close))   # C1 body low
    elif c2.low < c1.low and c1.low <= c2.close <= c1.high:
        direction = "LONG"
        stop = float(c2.low)
        target = float(max(c1.open, c1.close))    # C1 body high
    else:
        return None

    highs, lows = find_swings(df, lookback=2)
    if _trend(highs, lows) != direction:
        return None

    # Key level: swept a recent KL_LOOKBACK extreme (old high/low) OR an FVG.
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    i = len(df) - 1
    if direction == "SHORT":
        oldhl = h[i] >= h[max(0, i - kl_lookback):i].max()
    else:
        oldhl = l[i] <= l[max(0, i - kl_lookback):i].min()
    key_level = "old high/low sweep" if oldhl else (
        "FVG" if _fvg_near(df, i, direction) else None)
    if key_level is None:
        return None

    price = float(c2.close)
    if direction == "SHORT" and target >= price:
        return None
    if direction == "LONG" and target <= price:
        return None

    return {"direction": direction, "stop": stop, "target": target,
            "key_level": key_level}


def _at_key_level(df, i, direction, kl_lookback):
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    if direction == "SHORT":
        if h[i] >= h[max(0, i - kl_lookback):i].max():
            return "old high/low sweep"
    else:
        if l[i] <= l[max(0, i - kl_lookback):i].min():
            return "old low/high sweep"
    return "FVG" if _fvg_near(df, i, direction) else None


def detect_crt_aligned(htf_df, ltf_df, kl_lookback=KL_LOOKBACK,
                       align_window=3, ltf_sweep_lb=20, cisd_window=4,
                       min_confluence=1):
    """FAITHFUL timeframe-aligned entry (HTF forms the setup, LTF executes).

    Finds the most recent valid HTF CRT (within `align_window` HTF bars, with-
    trend, at a key level, not invalidated), then looks on the LTF for a sweep
    of a recent extreme + a single-candle CISD close. Fires ONLY when that entry
    confirms on the LAST CLOSED LTF candle (a fresh trigger, so live open-dedup
    opens it exactly once).

    `min_confluence` = how many key levels must STACK (the A+ quality filter):
    1 = any single key level, 2 = an A+ setup where two line up, etc. Uses the
    careful detectors in key_levels.py (old-high/low, FVG, rejection block).

    SL = just beyond the swept LTF extreme (per the doc's C3 entry). TP = the
    HTF C1 body. Returns {direction, entry, stop, target, key_level} or None.
    """
    if len(htf_df) < max(kl_lookback + 3, 30) or len(ltf_df) < ltf_sweep_lb + 6:
        return None

    highs, lows = find_swings(htf_df, lookback=2)
    trend = _trend(highs, lows)
    if trend is None:
        return None

    h = htf_df["high"].to_numpy(); l = htf_df["low"].to_numpy()
    o = htf_df["open"].to_numpy(); c = htf_df["close"].to_numpy()
    ts = htf_df["timestamp"].to_numpy()
    n = len(htf_df)

    setup = None
    for i in range(n - 1, max(n - 1 - align_window, kl_lookback), -1):
        c1_hi, c1_lo, c1_o, c1_c = h[i - 1], l[i - 1], o[i - 1], c[i - 1]
        if h[i] > c1_hi and c1_lo <= c[i] <= c1_hi:
            direction, protect, target = "SHORT", float(h[i]), float(min(c1_o, c1_c))
        elif l[i] < c1_lo and c1_lo <= c[i] <= c1_hi:
            direction, protect, target = "LONG", float(l[i]), float(max(c1_o, c1_c))
        else:
            continue
        if trend != direction:
            continue
        cnt, labels = key_levels.count_key_levels(
            htf_df, i, direction, swings=(highs, lows))
        if cnt < min_confluence:
            continue
        key = " + ".join(labels)
        htf_ms = int(ts[i] - ts[i - 1])
        setup = (i, direction, protect, target, key, htf_ms)
        break
    if setup is None:
        return None
    i, direction, protect, target, key, htf_ms = setup

    c2_close = int(ts[i]) + htf_ms
    end_ms = c2_close + align_window * htf_ms
    lts = ltf_df["timestamp"].to_numpy()
    lo_ = ltf_df["open"].to_numpy(); lh = ltf_df["high"].to_numpy()
    ll = ltf_df["low"].to_numpy(); lc = ltf_df["close"].to_numpy()
    m = len(ltf_df)
    last = m - 1

    import bisect
    j = max(bisect.bisect_left(lts.tolist(), c2_close), ltf_sweep_lb)
    while j < m - 1 and lts[j] < end_ms:
        if direction == "SHORT" and lh[j] > protect:
            return None                                   # CRT invalidated
        if direction == "LONG" and ll[j] < protect:
            return None
        if direction == "SHORT":
            if lh[j] > lh[j - ltf_sweep_lb:j].max():        # swept a recent high
                body_lo = min(lo_[j], lc[j])
                for k in range(j + 1, min(j + 1 + cisd_window, m)):
                    if lc[k] < body_lo:                     # single-candle CISD down
                        if k != last:
                            return None                     # triggered earlier, not now
                        entry = float(lc[k])
                        if target >= entry:
                            return None
                        return {"direction": direction, "entry": entry,
                                "stop": float(lh[j]), "target": target, "key_level": key}
        else:
            if ll[j] < ll[j - ltf_sweep_lb:j].min():
                body_hi = max(lo_[j], lc[j])
                for k in range(j + 1, min(j + 1 + cisd_window, m)):
                    if lc[k] > body_hi:
                        if k != last:
                            return None
                        entry = float(lc[k])
                        if target <= entry:
                            return None
                        return {"direction": direction, "entry": entry,
                                "stop": float(ll[j]), "target": target, "key_level": key}
        j += 1
    return None
