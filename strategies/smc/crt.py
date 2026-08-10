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

import bisect
import math

from strategies.smc.market_structure import find_swings
from strategies.smc import key_levels

KL_LOOKBACK = 20   # bars back a sweep must exceed to count as an "old high/low"
OTE_FIB = 0.75     # entry inside the 0.705-0.786 discount/premium retracement —
                   # the group's actual "enter on the retest" method (backtest:
                   # -0.15%/tr vs -0.53 for the breakout entry). Better R:R.


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


def detect_crt_setup(df, kl_lookback=KL_LOOKBACK, min_confluence=1, min_rr=1.0):
    """HTF CRT SETUP for the live feed — the way the group actually trades it:
    fire the moment a valid CRT prints on the LAST closed HTF candle, with the
    full textbook trade plan, RELAXED from the strict aligned model so it catches
    the setups the group takes:
      * ranges are ALLOWED — only a CLEARLY OPPOSITE market-structure trend is
        rejected (the group trades CRT in ranges, e.g. LTC off a swept range low);
      * needs >= min_confluence key levels (default 1, not the A+ 2);
      * no lower-timeframe fill requirement — you get the alert when the setup
        forms and take the entry with your own judgement (the discretion is the
        edge). Textbook plan: enter at C2 close, stop = C2's protected extreme,
        target = C1 body (the shared 2R-partial + BE runner books it).
    Returns {direction, entry, stop, target, key_level, confluence} or None.
    """
    if len(df) < max(kl_lookback + 3, 30):
        return None
    c1 = df.iloc[-2]
    c2 = df.iloc[-1]
    if c2.high > c1.high and c1.low <= c2.close <= c1.high:
        direction = "SHORT"; stop = float(c2.high); target = float(min(c1.open, c1.close))
    elif c2.low < c1.low and c1.low <= c2.close <= c1.high:
        direction = "LONG"; stop = float(c2.low); target = float(max(c1.open, c1.close))
    else:
        return None

    highs, lows = find_swings(df, lookback=2)
    trend = _trend(highs, lows)
    if trend is not None and trend != direction:
        return None                      # don't fight a CLEAR opposite trend; ranges OK

    i = len(df) - 1
    cnt, labels = key_levels.count_key_levels(df, i, direction, swings=(highs, lows))
    if cnt < min_confluence:
        return None

    entry = float(c2.close)
    if direction == "SHORT" and target >= entry:
        return None
    if direction == "LONG" and target <= entry:
        return None
    risk = abs(entry - stop)
    if risk <= 0 or abs(target - entry) / risk < min_rr:
        return None                      # skip objectively poor R:R to the C1 body
    return {"direction": direction, "entry": entry, "stop": stop, "target": target,
            "key_level": " + ".join(labels) or "key level", "confluence": cnt,
            "regime": "range" if trend is None else "with-trend"}


def _round_step(p):
    """Spacing of the 'obvious' round numbers around price p (one tenth of its
    decade): 65043 -> 1000 (65000, 66000), 2.077 -> 0.1 (2.0, 2.1),
    0.1003 -> 0.01 (0.10, 0.11). Those figures are where the crowd's stops sit."""
    return 10.0 ** (math.floor(math.log10(p)) - 1) if p > 0 else 0.0


def _sl_beyond_wick(stop, direction, buffer_pct=0.0015, clear_pct=0.0035):
    """Push the stop slightly BEYOND the sweep wick, onto a deliberately ODD price.

    Price very often runs a little past the sweep extreme before turning, and it
    is drawn to round figures because that is where everyone's stops are resting.
    A stop sitting exactly on the wick — or worse, just short of a round number —
    is the easiest liquidity in the market. So:
      1. step a small buffer past the wick (away from entry),
      2. if an obvious round level sits just beyond that, jump clear of it,
      3. settle on an odd, non-round tick (5 significant digits).
    Every adjustment moves AWAY from entry, so the stop can only widen, never
    tighten. SHORT stops sit above price, LONG stops below.
    """
    if stop <= 0:
        return stop
    up = direction == "SHORT"            # which way is "away from entry"
    sign = 1.0 if up else -1.0
    s = stop * (1.0 + sign * buffer_pct)

    # 2) never park just short of a round number - price hunts straight through it
    step = _round_step(s)
    if step > 0:
        nxt = (math.ceil(s / step) if up else math.floor(s / step)) * step
        if abs(nxt - s) <= s * clear_pct:
            s = nxt * (1.0 + sign * buffer_pct)

    # 3) land on an odd, arbitrary-looking tick rather than a clean figure
    tick = 10.0 ** (math.floor(math.log10(s)) - 4)
    n = int(math.ceil(s / tick) if up else math.floor(s / tick))
    while n % 10 not in (1, 3, 7, 9):
        n += 1 if up else -1
    return n * tick


def detect_crt_scout(df, min_confluence=1, min_rr=1.0, swing_lb=5,
                     level_lookback=40, min_age=5, spike_mult=2.5,
                     min_stop_pct=0.015, sl_buffer_pct=0.0015,
                     kl_lookback=KL_LOOKBACK):
    """SCOUT detector — a REAL liquidity-sweep CRT you can validate on the chart.

    The last CLOSED candle must sweep a GENUINE prior SWING high/low (a level
    that stood for >= `min_age` bars — real liquidity a human would mark) and
    CLOSE back through it (the turtle-soup rejection):
      * SHORT: the candle's WICK poked above an old swing HIGH but its CLOSE is
        back BELOW that high.
      * LONG:  the wick poked below an old swing LOW but the CLOSE is back ABOVE.
    So the swept level IS the key level (no phantom distant pivots), and a mere
    poke above the *previous candle* no longer qualifies. Direction = the
    reversal (NO trend filter).

    Entry = the candle's close. Stop = slightly BEYOND the sweep wick, nudged onto
    an odd/non-round price (see `_sl_beyond_wick`) so the common overshoot and the
    round-number stop hunt don't tag it. Target
    (TP2) = the nearest prior opposing swing level (the draw-on-liquidity) that
    yields >= `min_rr`; TP1 = halfway there. Confluence = the swept swing (always
    1) + any FVG / rejection block also at the sweep. Returns
    {direction, entry, stop, tp1, tp2, rr, key_level, confluence, level,
     signal_ts} or None.
    """
    if len(df) < max(level_lookback + swing_lb + 3, 55):
        return None
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    o = df["open"].to_numpy()
    c = df["close"].to_numpy()
    i = len(df) - 1
    s_hi, s_lo, s_close = float(h[i]), float(l[i]), float(c[i])

    highs, lows = find_swings(df, lookback=swing_lb)

    # Typical candle range in the window — used to reject OUTLIER SPIKE wicks
    # (flash-crash / data-glitch candles) that no human would mark as a level.
    a = max(0, i - level_lookback)
    rng = sorted(float(h[k] - l[k]) for k in range(a, i))
    med_range = rng[len(rng) // 2] if rng else 0.0

    def _real_level(idx, is_low):
        """A swing level is REAL only if the candle that made it isn't an outlier
        spike — the wick BEYOND the body must be within spike_mult x the typical
        range (a flash-crash wick to a price price never actually traded around
        is not a level)."""
        if med_range <= 0:
            return True
        if is_low:
            wick = min(o[idx], c[idx]) - l[idx]      # lower wick below the body
        else:
            wick = h[idx] - max(o[idx], c[idx])      # upper wick above the body
        return wick <= spike_mult * med_range

    def _untapped_high(idx, p):
        """The level held as resting liquidity until now — no candle BETWEEN its
        formation and the sweep candle already traded through it."""
        seg = h[idx + 1:i]
        return len(seg) == 0 or seg.max() <= p

    def _untapped_low(idx, p):
        seg = l[idx + 1:i]
        return len(seg) == 0 or seg.min() >= p

    # A real, UNTAPPED prior swing HIGH the last candle wicked above but closed
    # back below (the first breach of that resting liquidity).
    swept_highs = [p for (idx, p) in highs
                   if i - level_lookback <= idx <= i - min_age and s_hi > p > s_close
                   and _real_level(idx, False) and _untapped_high(idx, p)]
    swept_lows = [p for (idx, p) in lows
                  if i - level_lookback <= idx <= i - min_age and s_lo < p < s_close
                  and _real_level(idx, True) and _untapped_low(idx, p)]
    short_lvl = max(swept_highs) if swept_highs else None    # nearest swept high
    long_lvl = min(swept_lows) if swept_lows else None        # nearest swept low

    if short_lvl is not None and long_lvl is not None:
        # rare outside-bar sweeping both — take the tighter (stronger) rejection
        direction = "SHORT" if (short_lvl - s_close) <= (s_close - long_lvl) else "LONG"
    elif short_lvl is not None:
        direction = "SHORT"
    elif long_lvl is not None:
        direction = "LONG"
    else:
        return None
    level = short_lvl if direction == "SHORT" else long_lvl

    entry = s_close
    # Stop goes slightly BEYOND the sweep wick, on an odd (non-round) price —
    # price commonly runs a touch past the extreme, and round figures are hunted.
    stop = _sl_beyond_wick(s_hi if direction == "SHORT" else s_lo,
                           direction, buffer_pct=sl_buffer_pct)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    # Reject noise-level stops: when the reversal candle closes near its own sweep
    # extreme the stop sits right on top of the entry (a fraction of a %) and gets
    # tagged by noise. Require the stop to be at least `min_stop_pct` from entry.
    if entry > 0 and risk / entry < min_stop_pct:
        return None

    # Range = the swept level (C1 origin) -> the opposing pool (draw-on-liquidity).
    # TP1 = 50% of that range (the first-target rule, measured from C1's START,
    # i.e. the swept level — NOT halfway from entry). TP2 = the pool. The setup
    # must be UNTOUCHED with ROOM: the close must still sit beyond the 50% so the
    # first target is genuinely ahead, and the R:R to that 50% must be >= min_rr.
    if direction == "SHORT":
        pools = sorted([p for (idx, p) in lows
                        if i - level_lookback <= idx <= i - 1 and p < entry], reverse=True)
    else:
        pools = sorted([p for (idx, p) in highs
                        if i - level_lookback <= idx <= i - 1 and p > entry])
    tp1 = tp2 = None
    for pool in pools:
        mid = (level + pool) / 2.0                 # 50% of the C1 range
        has_room = (entry > mid) if direction == "SHORT" else (entry < mid)
        if not has_room:
            continue                               # 50% already inside the move -> no room
        if abs(entry - mid) / risk >= min_rr:      # good R:R to the FIRST target
            tp1, tp2 = float(mid), float(pool)
            break
    if tp1 is None:
        return None
    rr = round(abs(tp1 - entry) / risk, 2)         # R:R to the 50% (first target)

    labels = [f"swept old {'high' if direction == 'SHORT' else 'low'} @ {level:.6g}"]
    conf = 1
    if key_levels.at_fvg(df, i, direction):
        conf += 1
        labels.append("FVG")
    if key_levels.at_rejection_block(df, i, direction, swings=(highs, lows)):
        conf += 1
        labels.append("rejection block")
    if conf < min_confluence:
        return None

    return {"direction": direction, "entry": float(entry), "stop": float(stop),
            "tp1": float(tp1), "tp2": tp2, "rr": rr,
            "key_level": " + ".join(labels), "confluence": conf,
            "level": float(level), "signal_ts": int(df["timestamp"].iloc[i])}


def _smt_confirms(df, ref_df, i, direction):
    """SMT (cross-asset divergence): at the C2 bar, did the REFERENCE asset NOT
    sweep the same way? (divergence in the CRT's favour). Aligns by timestamp.
    Returns False if no aligned reference bar (can't confirm)."""
    if ref_df is None:
        return False
    ts_i = int(df["timestamp"].iloc[i])
    rts = ref_df["timestamp"].to_numpy().tolist()
    j = bisect.bisect_left(rts, ts_i)
    if j >= len(rts) or int(rts[j]) != ts_i or j < 1:
        return False
    rlo = ref_df["low"].to_numpy(); rhi = ref_df["high"].to_numpy()
    if direction == "LONG":
        return not (rlo[j] < rlo[j - 1])     # coin swept its low; ref did NOT
    return not (rhi[j] > rhi[j - 1])          # coin swept its high; ref did NOT


def detect_crt_enhanced(df, ref_df=None, kl_lookback=KL_LOOKBACK):
    """The VALIDATED enhanced DAILY CRT (backtest: +0.25%/tr, 72% win, both
    walk-forward halves positive, broad). Fires on the LAST CLOSED candle (=C3)
    when a full confirmed setup is present:
      C1 (=-3) range -> C2 (=-2) sweeps C1's high/low AND closes back inside ->
      C3 (=-1) CONFIRMS by closing beyond C2's body in the trade direction ->
      with-trend (market structure) + at a key level + SMT-confirmed (BTC/ETH).
    Entry = C3 close; stop = C2's swept extreme; TP1 = 50% of C1's range (bank
    50% + move to break-even); TP2 = the OPPOSITE extreme of C1's range. No R:R,
    no timeframe alignment — daily only. Returns the trade dict or None.
    """
    if len(df) < max(kl_lookback + 4, 34):
        return None
    c1 = df.iloc[-3]; c2 = df.iloc[-2]; c3 = df.iloc[-1]
    c1_hi = float(c1.high); c1_lo = float(c1.low)

    # C2 = a valid CRT of C1 (sweep + close back inside)
    if c2.high > c1_hi and c1_lo <= c2.close <= c1_hi:
        direction, stop = "SHORT", float(c2.high)
    elif c2.low < c1_lo and c1_lo <= c2.close <= c1_hi:
        direction, stop = "LONG", float(c2.low)
    else:
        return None

    # C3 confirmation: close beyond C2's body in the trade direction
    c2_body_lo = min(float(c2.open), float(c2.close))
    c2_body_hi = max(float(c2.open), float(c2.close))
    if direction == "SHORT" and not (c3.close < c2_body_lo):
        return None
    if direction == "LONG" and not (c3.close > c2_body_hi):
        return None

    # with-trend (market structure)
    highs, lows = find_swings(df, lookback=2)
    if _trend(highs, lows) != direction:
        return None

    # at a key level (checked on the C2 sweep bar)
    i2 = len(df) - 2
    cnt, labels = key_levels.count_key_levels(df, i2, direction, swings=(highs, lows))
    if cnt < 1:
        return None

    # SMT filter (compare the reference asset at the C2 bar)
    if not _smt_confirms(df, ref_df, i2, direction):
        return None

    # targets from C1's range: TP1 = 50%, TP2 = opposite extreme
    mid = (c1_hi + c1_lo) / 2.0
    opp = c1_hi if direction == "LONG" else c1_lo
    entry = float(c3.close)
    mid_beyond = (mid > entry) if direction == "LONG" else (mid < entry)
    tp1 = mid if mid_beyond else opp                  # if 50% already passed, run to opp
    tp2 = opp
    if direction == "LONG" and not (stop < entry < tp2):
        return None
    if direction == "SHORT" and not (tp2 < entry < stop):
        return None

    return {"direction": direction, "entry": entry, "stop": stop,
            "tp1": tp1, "tp2": tp2, "key_level": " + ".join(labels) or "key level",
            "confluence": cnt}


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
    # Walk the LTF: find a sweep + single-candle CISD that FORMS the OTE entry
    # (the setup), then fire ONLY when price actually RETRACES into that limit on
    # the LAST closed bar (a real fill) — not the instant the CISD prints at a
    # level price hasn't reached. Mirrors OTE's detect_ote_live: kills the phantom
    # instant-win + the re-open/re-alert churn (one real fill = one trade).
    while j < last and lts[j] < end_ms:
        if direction == "SHORT" and lh[j] > protect:
            return None                                   # CRT invalidated
        if direction == "LONG" and ll[j] < protect:
            return None

        entry = stop = cisd = None
        if direction == "SHORT" and lh[j] > lh[j - ltf_sweep_lb:j].max():
            body_lo = min(lo_[j], lc[j])
            for k in range(j + 1, min(j + 1 + cisd_window, m)):
                if lc[k] < body_lo:                         # single-candle CISD down
                    imp_lo = float(ll[j:k + 1].min())       # OTE: enter on the retest
                    entry = imp_lo + (float(lh[j]) - imp_lo) * OTE_FIB
                    stop = float(lh[j]); cisd = k
                    break
        elif direction == "LONG" and ll[j] < ll[j - ltf_sweep_lb:j].min():
            body_hi = max(lo_[j], lc[j])
            for k in range(j + 1, min(j + 1 + cisd_window, m)):
                if lc[k] > body_hi:
                    imp_hi = float(lh[j:k + 1].max())
                    entry = imp_hi - (imp_hi - float(ll[j])) * OTE_FIB
                    stop = float(ll[j]); cisd = k
                    break

        if entry is not None and cisd < last:
            room = (target < entry) if direction == "SHORT" else (target > entry)
            if room:
                # From the CISD bar to the last bar, the limit must NOT have filled
                # yet and the setup must NOT be invalidated (stop) or completed
                # (target). The last closed bar must be the FIRST fill.
                spent = False
                for f in range(cisd + 1, last):
                    if direction == "SHORT":
                        if lh[f] >= stop or ll[f] <= target or lh[f] >= entry:
                            spent = True; break
                    else:
                        if ll[f] <= stop or lh[f] >= target or ll[f] <= entry:
                            spent = True; break
                if not spent:
                    if direction == "SHORT" and lh[last] >= entry and lh[last] < stop and ll[last] > target:
                        return {"direction": direction, "entry": entry,
                                "stop": stop, "target": target, "key_level": key}
                    if direction == "LONG" and ll[last] <= entry and ll[last] > stop and lh[last] < target:
                        return {"direction": direction, "entry": entry,
                                "stop": stop, "target": target, "key_level": key}
        j += 1
    return None
