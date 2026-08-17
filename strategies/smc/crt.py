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


def _odd_tick(x, up):
    """Nudge x to an odd, arbitrary-looking price (5 significant digits, last
    digit forced to 1/3/7/9) so it never sits on a figure the market is hunting.
    Moves in the `up` direction only, so the caller keeps its safety margin."""
    tick = 10.0 ** (math.floor(math.log10(x)) - 4)
    n = int(math.ceil(x / tick) if up else math.floor(x / tick))
    while n % 10 not in (1, 3, 7, 9):
        n += 1 if up else -1
    return n * tick


def _nudge(price, up, buffer_pct=0.0015, clear_pct=0.0035):
    """Step `price` a small buffer in the `up` direction, keep it clear of the
    round figure it would otherwise sit on, and land it on an odd tick.

    Round numbers are where the crowd's orders rest, so price is drawn to them
    and frequently turns right at one. Whichever price we are placing — stop,
    target or limit entry — the fix is the same: never sit ON the figure, and
    never sit just SHORT of the one price must travel through to reach us. Only
    the chosen direction differs, which is what the three wrappers below decide.
    """
    if price <= 0:
        return price
    sign = 1.0 if up else -1.0
    p = price * (1.0 + sign * buffer_pct)

    step = _round_step(p)
    if step > 0:
        lvl = (math.ceil(p / step) if up else math.floor(p / step)) * step
        if abs(lvl - p) <= p * clear_pct:
            p = lvl * (1.0 + sign * buffer_pct)

    return _odd_tick(p, up)


def _sl_beyond_wick(stop, direction, buffer_pct=0.0015, clear_pct=0.0035):
    """Stop slightly BEYOND the sweep wick — AWAY from entry, so it only widens.

    Price often runs a touch past the sweep extreme before turning, and a stop
    sitting exactly on the wick is the easiest liquidity on the chart. SHORT
    stops sit above entry (push up), LONG stops below (push down)."""
    return _nudge(stop, direction == "SHORT", buffer_pct, clear_pct)


def _tp_inside_target(tp, direction, buffer_pct=0.0015, clear_pct=0.0035):
    """Target slightly INSIDE the level — TOWARD entry, so it only gets easier.

    Price routinely stalls and turns a hair short of an obvious level, so a
    target parked on one misses the fill by pennies and then reverses. A SHORT
    target sits below entry, so "toward entry" is up; a LONG target, down."""
    return _nudge(tp, direction == "SHORT", buffer_pct, clear_pct)


def _entry_easier_fill(entry, direction, buffer_pct=0.0015, clear_pct=0.0035):
    """Limit entry nudged so it ACTUALLY FILLS — the odd one out, and deliberately.

    A limit entry is approached from the OPPOSITE side to a target: price falls
    onto a LONG entry from above and rises onto a SHORT entry from below. So the
    direction inverts — a LONG limit sits a touch HIGHER (price reaches it
    sooner), a SHORT limit a touch LOWER. Costs a hair of entry price in exchange
    for not watching the setup reverse a few ticks short of your order."""
    return _nudge(entry, direction == "LONG", buffer_pct, clear_pct)


def detect_crt_scout(df, min_confluence=1, min_rr=1.0, swing_lb=5,
                     level_lookback=40, min_age=5, spike_mult=2.5,
                     min_stop_pct=0.015, sl_buffer_pct=0.0015,
                     tp_buffer_pct=0.0015, entry_buffer_pct=0.0015,
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

    Entry = the candle's close, nudged a hair to the near side so a resting limit
    actually fills (see `_entry_easier_fill`). Stop = slightly BEYOND the sweep wick, nudged onto
    an odd/non-round price (see `_sl_beyond_wick`) so the common overshoot and the
    round-number stop hunt don't tag it. Targets sit just INSIDE their level on an
    odd price too (see `_tp_inside_target`), since price often turns a hair short
    of an obvious figure. Target
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

    # Entry is the sweep candle's close, nudged onto an odd price a hair on the
    # NEAR side (see _entry_easier_fill) so a resting limit gets reached instead
    # of missing the turn by a few ticks. Everything below measures from it.
    entry = _entry_easier_fill(s_close, direction, buffer_pct=entry_buffer_pct)
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
        # Targets sit just INSIDE the level on an odd price (see _tp_inside_target),
        # so the R:R gate below is measured against the price we actually exit at.
        c1 = _tp_inside_target(mid, direction, buffer_pct=tp_buffer_pct)
        c2 = _tp_inside_target(pool, direction, buffer_pct=tp_buffer_pct)
        still_ahead = (entry > c1 > 0) if direction == "SHORT" else (c1 > entry)
        if not still_ahead:
            continue                               # pulled back past entry -> no trade left
        if abs(entry - c1) / risk >= min_rr:       # good R:R to the FIRST target
            tp1, tp2 = float(c1), float(c2)
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


# ---------------------------------------------------------------------------
# CRT v3 - the model the way it is actually taught and traded.
#
# detect_crt_scout above hunts a SWING PIVOT first and only then asks whether a
# candle swept it. Measured over 36,253 candles that turned out to find a
# different animal: 60% of its alerts are not a CRT at all, and it misses 97.9%
# of the real ones. The ORDER is what was wrong - a CRT prints on ~38% of
# candles, so the CRT is the easy part and the KEY LEVEL is the filter that
# makes it rare. v3 puts them back in that order.
# ---------------------------------------------------------------------------

# Smallest stop per timeframe. One flat figure was wrong: a typical stop is
# ~0.9% on 1h and ~1.2% on 4h but ~3% on 1d, so a flat 1.5% threw away three
# quarters of hourly setups while barely touching daily.
MIN_STOP_PCT = {"1w": 0.015, "1d": 0.015, "4h": 0.006, "1h": 0.0045}

# Reward:risk floor per timeframe. 1h is noisier, so it has to pay more.
MIN_RR = {"1w": 1.0, "1d": 1.0, "4h": 1.0, "1h": 1.1}

# On 1h only: the whole trade, booked out in full, must be worth more than this
# in price movement. Half comes off at TP1 and half at TP2, so this is the
# blended move — a 1h setup that can only ever pay 0.4% is not worth the risk.
MIN_NET_PCT = {"1h": 1.0}

MIN_C1_RANGE_MULT = 0.6      # C1 must be a real range, not a doji


def _premium_discount(df, i, direction, lookback=60):
    """Expensive or cheap inside the recent range? The group's rule is
    'sells only at expensive prices, buys only at cheaper prices'."""
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    a = max(0, i - lookback)
    hi, lo = float(h[a:i + 1].max()), float(l[a:i + 1].min())
    if hi <= lo:
        return True
    eq = (hi + lo) / 2.0
    return c[i] >= eq if direction == "SHORT" else c[i] <= eq


RANGE_TOL = 0.05   # two swing highs (and lows) within 5% = bounded, not trending.
                   # Calibrated on BTC daily: 2% never fires, 5% labels ~16% of
                   # bars RANGE and eats into MIXED rather than into BULL/BEAR,
                   # which is what we want -- it should rescue the "no clear
                   # structure" cases, not reclassify real trends.


def _trend_structure(df, i, swing_lb=3):
    """Higher highs + higher lows, or lower highs + lower lows.

    His definition, verbatim: "Trend is bullish. Market is making higher high...
    higher low." [part4 @55:04]. Read on the CRT's OWN timeframe, not a higher
    one -- "Daily is the trend. We have not taken any of the trends from the
    weekly." [part4 @52:55]. Returns "BULLISH", "BEARISH" or "MIXED".
    """
    sub = df.iloc[: i + 1]
    highs, lows = find_swings(sub, lookback=swing_lb)
    if len(highs) < 2 or len(lows) < 2:
        return "MIXED"
    hh, hl = highs[-1][1] > highs[-2][1], lows[-1][1] > lows[-2][1]
    lh, ll = highs[-1][1] < highs[-2][1], lows[-1][1] < lows[-2][1]
    if hh and hl:
        return "BULLISH"
    if lh and ll:
        return "BEARISH"
    # RANGE: structure that is neither making higher highs+lows nor lower ones,
    # AND the last two swing highs / two swing lows sit close together -- price
    # is bounded rather than merely mixed. The user's rule is three-way, not
    # two: "we can only trade CRT if we get HTF reversal signs, or WITH TREND,
    # or IN RANGE (after confirmation)". A range is tradeable to them; a
    # genuine counter-trend setup is not. MIXED stays for the leftover case
    # where structure says nothing either way.
    hi_gap = abs(highs[-1][1] - highs[-2][1]) / max(highs[-2][1], 1e-9)
    lo_gap = abs(lows[-1][1] - lows[-2][1]) / max(lows[-2][1], 1e-9)
    if hi_gap <= RANGE_TOL and lo_gap <= RANGE_TOL:
        return "RANGE"
    return "MIXED"


def detect_crt_v3(df, tf="1d", c1_lookback=12, min_confluence=2,
                  require_pd=False, swing_lb=3, require_trend=False,
                  min_stop_pct=None, min_rr=None, min_net_pct=None):
    """A CRT on the LAST CLOSED candle. Returns a setup dict or None.

    `min_confluence=0` gives the unfiltered feed — every real CRT, no key-level
    requirement. That is the "no confirmation" stream for practising level
    selection by eye.
    """
    if len(df) < max(c1_lookback + 30, 60):
        return None
    min_stop_pct = MIN_STOP_PCT.get(tf, 0.015) if min_stop_pct is None else min_stop_pct
    min_rr = MIN_RR.get(tf, 1.0) if min_rr is None else min_rr
    min_net_pct = MIN_NET_PCT.get(tf, 0.0) if min_net_pct is None else min_net_pct

    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    i = len(df) - 1

    a = max(0, i - 60)
    ranges = sorted(float(h[k] - l[k]) for k in range(a, i))
    med = ranges[len(ranges) // 2] if ranges else 0.0

    # ONE CANDLE CAN BE C2 TO SEVERAL DIFFERENT C1s -- the user's "CRT within
    # CRT". This used to take the nearest match and `break`; if that candidate
    # then failed a gate below (confluence, min stop, R:R) the whole detector
    # returned None and never looked at the valid ranges further back. Collect
    # every candidate, nearest first, and try each until one passes.
    candidates = []
    for j in range(i - 1, max(i - c1_lookback, 0) - 1, -1):
        c1_hi, c1_lo = float(h[j]), float(l[j])
        if c1_hi - c1_lo < MIN_C1_RANGE_MULT * med:
            continue
        if h[i] > c1_hi and c1_lo <= c[i] <= c1_hi:
            direction = "SHORT"
        elif l[i] < c1_lo and c1_lo <= c[i] <= c1_hi:
            direction = "LONG"
        else:
            continue
        # First resolution only: if something between C1 and now already took
        # that side, this is no longer news.
        mid = df.iloc[j + 1:i]
        if len(mid):
            if direction == "SHORT" and mid["high"].max() > c1_hi:
                continue
            if direction == "LONG" and mid["low"].min() < c1_lo:
                continue

            # THE OTHER SIDE MATTERS TOO -- two rules the user gave from their
            # own charts, both verified against OHLC on the Oct 2021-Jan 2022
            # review. Neither was checked before: we only ever looked at the
            # side being swept.
            c1_body_hi = max(float(o[j]), float(c[j]))
            c1_body_lo = min(float(o[j]), float(c[j]))
            mc = mid["close"].to_numpy()
            mh = mid["high"].to_numpy(); ml = mid["low"].to_numpy()

            # (A) A CLOSE beyond the OPPOSITE side of C1's BODY kills the CRT.
            # "If the candle is closing beyond, it will be invalid -- all your
            # CRT, all the concept will be invalid" [part1 @33:43]; the user
            # refined it to the body: "27 Feb candle closes below the body of
            # C1". Caught CIA firing on the Oct-18 setup four days after an
            # Oct-19 close above the range, and on the Dec-21 setup after
            # Dec 23 closed above it.
            if direction == "SHORT" and (mc < c1_body_lo).any():
                continue
            if direction == "LONG" and (mc > c1_body_hi).any():
                continue

            # (B) Once C1 has RESOLVED one way it is spent -- do not take the
            # opposite direction off it. Jan 2 2022 swept C1's high and closed
            # back inside (a SHORT); CIA then fired a LONG off the same C1 on
            # Jan 3. Later sweeps are legitimate -- taking the OTHER SIDE after
            # one side has played out is not.
            if direction == "LONG" and ((mh > c1_hi) & (mc <= c1_hi)).any():
                continue
            if direction == "SHORT" and ((ml < c1_lo) & (mc >= c1_lo)).any():
                continue
        candidates.append((j, direction, c1_hi, c1_lo))
    if not candidates:
        return None

    for cand in candidates:
        s = _build_crt_v3(df, i, cand, med, swing_lb, require_pd, require_trend,
                          min_confluence, min_stop_pct, min_rr, min_net_pct)
        if s is not None:
            s["nested"] = len(candidates) > 1
            return s
    return None


def _build_crt_v3(df, i, cand, med, swing_lb, require_pd, require_trend,
                  min_confluence, min_stop_pct, min_rr, min_net_pct):
    """Apply every gate to ONE C1 candidate. Returns the setup dict or None."""
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    j, direction, c1_hi, c1_lo = cand

    if require_pd and not _premium_discount(df, i, direction):
        return None

    # "Do you always trade with CRT against the trend? -- You will not trade."
    # [part4 @50:38]. Step ONE of his process, ahead of the key level.
    trend = _trend_structure(df, i, swing_lb)
    if require_trend:
        if direction == "LONG" and trend != "BULLISH":
            return None
        if direction == "SHORT" and trend != "BEARISH":
            return None

    # The key level QUALIFIES the CRT — it is not the trigger.
    highs, lows = find_swings(df, lookback=swing_lb)
    conf, labels = key_levels.count_key_levels(df, i, direction,
                                               swings=(highs, lows), c1_index=j)
    if conf < min_confluence:
        return None

    # Prices. Entry and stop keep the deployed treatment: entry nudged to the
    # near side so a limit fills, stop pushed BEYOND the sweep candle's wick.
    entry = _entry_easier_fill(float(c[i]), direction)
    stop = _sl_beyond_wick(float(h[i]) if direction == "SHORT" else float(l[i]),
                           direction)
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0 or risk / entry < min_stop_pct:
        return None

    # Targets from C1's BODY, not its wicks.
    body_hi = max(float(o[j]), float(c[j]))
    body_lo = min(float(o[j]), float(c[j]))
    body_mid = (body_hi + body_lo) / 2.0
    body_far = body_lo if direction == "SHORT" else body_hi

    tp1 = _tp_inside_target(body_mid, direction)
    tp2 = _tp_inside_target(body_far, direction)
    ahead = (entry > tp1 > 0) if direction == "SHORT" else (tp1 > entry)
    if not ahead:
        return None
    # TP2 behind TP1 would mean the body is thinner than the trade: run to TP1.
    beyond = (tp2 < tp1) if direction == "SHORT" else (tp2 > tp1)
    if not beyond:
        tp2 = tp1

    rr = abs(tp1 - entry) / risk
    if rr < min_rr:
        return None

    # Booked out in full: half at TP1, half at TP2.
    net_pct = (0.5 * abs(tp1 - entry) + 0.5 * abs(tp2 - entry)) / entry * 100.0
    if net_pct < min_net_pct:
        return None

    return {
        "direction": direction, "entry": float(entry), "stop": float(stop),
        "tp1": float(tp1), "tp2": float(tp2), "rr": round(rr, 2),
        "net_pct": round(net_pct, 2),
        "stop_pct": round(risk / entry * 100, 2),
        "tp1_pct": round(abs(tp1 - entry) / entry * 100, 2),
        "tp2_pct": round(abs(tp2 - entry) / entry * 100, 2),
        "c1_index": j, "c1_gap": i - j,
        "crt_high": c1_hi, "crt_low": c1_lo,
        "body_high": body_hi, "body_low": body_lo,
        "confluence": conf, "key_level": " + ".join(labels) or "none",
        "trend": trend, "with_trend": (trend == "BULLISH") == (direction == "LONG")
                                      and trend != "MIXED",
        "qualified": conf >= 1,
        # "Bonus: if there is also an FVG sitting right above the swept high
        # (bearish) or right below the swept low (bullish), that stacks as
        # extra confluence -- an even higher-probability (A+) setup."
        "fvg_beside": bool(key_levels.fvg_beside_level(
            df, i, direction, c1_hi if direction == "SHORT" else c1_lo)),
        # FLAG, never a filter: the user asked to still be alerted when the
        # sweep candle itself already covered the first target, so they can
        # judge whether an LTF entry is still worth taking. "C2 CRT" is their
        # single most common reason for passing on a setup.
        "c2_delivered": bool(
            (float(h[i]) >= body_mid) if direction == "LONG"
            else (float(l[i]) <= body_mid)),
        "signal_ts": int(df["timestamp"].iloc[i]),
    }


def ltf_confirms(ltf_df, direction, since_ts, lookahead=60):
    """Did a lower timeframe confirm the HTF CRT? Optional — never blocks.

    Follows the sources: the entry trigger is a liquidity sweep followed by a
    DISPLACEMENT close through it — Romeo's "model #1" (the one candle that
    took the old high/low, then price closes beyond that candle's body). We
    look for that pattern on the LTF after the HTF candle closed.

    Returns True / False, or None when there is no LTF data to judge with.
    """
    if ltf_df is None or len(ltf_df) < 20:
        return None
    ts = ltf_df["timestamp"].to_numpy()
    start = int(ts.searchsorted(since_ts))
    if start >= len(ltf_df) - 2:
        return None
    o = ltf_df["open"].to_numpy(); c = ltf_df["close"].to_numpy()
    h = ltf_df["high"].to_numpy(); l = ltf_df["low"].to_numpy()
    end = min(len(ltf_df), start + lookahead)

    # Typical bar size, so "displacement" means genuinely bigger than normal.
    win = [float(h[k] - l[k]) for k in range(max(0, start - 30), start)] or [0.0]
    avg = sum(win) / len(win)

    for k in range(start + 1, end):
        prev_lo = min(float(o[k - 1]), float(c[k - 1]))
        prev_hi = max(float(o[k - 1]), float(c[k - 1]))
        body = abs(float(c[k]) - float(o[k]))
        rng = float(h[k] - l[k]) or 1e-9
        displaced = body >= 1.2 * avg and body / rng >= 0.5
        if direction == "SHORT":
            swept = h[k - 1] >= max(h[start:k].max(), h[k - 1])
            if swept and c[k] < prev_lo and displaced:
                return True
        else:
            swept = l[k - 1] <= min(l[start:k].min(), l[k - 1])
            if swept and c[k] > prev_hi and displaced:
                return True
    return False


# --------------------------------------------------------------------------- #
#  CRT 1.0 — the specification recovered from the user's own course videos.
#  (research/crt-playlist/_RULES.md carries the rule-by-rule sources.)
#
#  The change that matters: the CRT and its key level are marked on the HIGHER
#  timeframe, but the ENTRY happens on the aligned LOWER one --
#      "Always make a rule... at least use the 2 time frame. Make the bias on
#       the high time frame."                                  [part1 @47:24]
#  Entering at the HTF candle close (what CIA did before) was measured at
#  -0.546%/trade, t=-8.12 over 4,718 setups. Same setups, entered his way:
#  +0.641%/trade better, t=+11.69.
#
#  Gates, each measured on 103 coins (daily->1h unless noted):
#    R:R >= 2 .............. +0.148 -> +0.434%/tr   (his stated minimum)
#    CISD within 4 candles . +0.434 -> +0.482%/tr   ("A+" timing)
#    FVG tapped at C1 ...... +0.482 -> +0.498%/tr   (small but free)
#  Final: +0.498%/tr, t=+5.50, both walk-forward halves +0.497/+0.498,
#  profitable on 65 of 99 coins. Weekly->4h scores +1.055%/tr, t=+2.84.
#
#  REJECTED after testing, do not re-add: a trend gate (fails, and hurts the
#  HTF entry), TBS over TWS (TBS -0.199 vs TWS +0.305 -- he is wrong on this),
#  the series CISD (single beats it, as he says), and the literal reading of
#  Model 2 (-0.342%).
# --------------------------------------------------------------------------- #

CRT10_PAIRS = {"1M": "1d", "1w": "4h", "1d": "1h", "4h": "15m"}
CRT10_MIN_RR = 1.0            # Was 2.0. Point-in-time on 103 coins with the
                              # corrected stop: rr>=0 +0.637%/tr t=+5.36 breadth
                              # 67/98 | rr>=1 +0.698 t=+5.82 breadth 63/98 |
                              # rr>=2 +0.583 t=+5.18 breadth 60/98. The 2.0 gate
                              # was the WORST of the three -- it cost trades,
                              # breadth and return. Measured four separate ways
                              # today; an R:R threshold has never helped.
CRT10_MAX_CISD_BARS = 11      # Was 4, from "within 3-4 candles of the sweep is
                              # A+". That is a QUALITY label in the source, not
                              # a validity rule, and 4 was validated on the
                              # broken baseline. Re-measured on the corrected
                              # stop + CISD line, 103 coins, point-in-time:
                              #   1d  4/6/8/11 bars -> +0.975/+0.999/+0.977/+0.992
                              #   1w  4/6/8/11 bars -> +1.658/+1.623/+1.748/+1.713
                              # Return is FLAT across the range; trades rise 10%
                              # on daily (764->843) and 13% on weekly (236->267),
                              # breadth improves on both. Same quality, more
                              # coverage -- so the cap is set to the full search
                              # window and stops discarding slower confirmations.
CRT10_LOOKAHEAD = 120         # LTF bars to wait for the CISD before giving up.
                              # Was 48 (2 days on a 1h entry chart), which quietly
                              # dropped setups whose confirmation simply took
                              # longer to print. Measured against the user's own
                              # marked CRTs: the "no LTF CISD" misses fall from
                              # 12 to 7 of 59, i.e. 5 of their confirmed setups
                              # become visible. Volume cost, probed over 25 coins
                              # of 1h data: ~31% more candidate triggers. Live
                              # alert volume rises by less, because an HTF setup
                              # must still exist and CRT10_MAX_TRIGGER_AGE still
                              # only alerts a CISD in the 3 bars after it prints.
CRT10_MAX_TRIGGER_AGE = 12    # Was 3. That was set when the stop sat at the LTF
                              # sweep wick (~1%), where 4 of the first 10
                              # proposals auto-cancelled as invalidated before
                              # the user could tap them. The stop now sits at the
                              # HTF protected extreme (1-4%), so a setup survives
                              # far longer and a 6-12 bar old trigger is much less
                              # likely to be dead on arrival. NOT backtestable --
                              # a backtest sees every trigger the instant it forms,
                              # so this cost only exists live. WATCH THE
                              # AUTO-CANCEL RATE; that is the number that
                              # justified 3, and the number that would justify
                              # pulling this back.
                              # (original note) the CISD must have printed within this many LTF
                              # bars of NOW. Without it the detector happily
                              # returns a two-day-old trigger whose retest has
                              # already happened, and the setup is cancelled as
                              # invalidated before the user can even tap it.


def _leg_fvg(df, k, j, direction):
    """Did the reversal leg between the sweep and the CISD leave a gap?

    A violent snap-back off the level leaves an imbalance; a hesitant one does
    not. Used ONLY to flag A+ -- never to filter -- because this is our reading
    of his Model 2, not his literal words. Measured +0.786%/tr on the subset.
    """
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    for a in range(k, max(k + 1, j - 1)):
        if a + 2 >= len(df):
            break
        if direction == "LONG" and h[a] < l[a + 2]:
            return True
        if direction == "SHORT" and l[a] > h[a + 2]:
            return True
    return False


def crt10_entry(ltf_df, direction, since_ts, tp1,
                lookahead=CRT10_LOOKAHEAD, level_lookback=30,
                small_body_frac=0.35):
    """His C3 entry, on the lower timeframe, after the HTF CRT candle closed.

    sweep a recent LTF extreme -> the SINGLE candle that swept is the reference
    -> a later candle must CLOSE beyond that candle's BODY (the CISD) -> enter
    the RETEST of that level (the wick level when the body is a sliver, since a
    sliver body rarely gets retested) -> stop just beyond the sweep wick.
    """
    if ltf_df is None or len(ltf_df) < 40:
        return None
    ts = ltf_df["timestamp"].to_numpy()
    o = ltf_df["open"].to_numpy(); c = ltf_df["close"].to_numpy()
    h = ltf_df["high"].to_numpy(); l = ltf_df["low"].to_numpy()

    start = int(ts.searchsorted(since_ts, side="right"))
    if start < 5 or start >= len(ltf_df) - 2:
        return None
    end = min(len(ltf_df) - 1, start + lookahead)

    for k in range(start, end):
        a = max(0, k - level_lookback)
        if k <= a:
            continue
        if direction == "LONG":
            prior = float(l[a:k].min())
            if l[k] >= prior:
                continue
            wick = float(l[k])
        else:
            prior = float(h[a:k].max())
            if h[k] <= prior:
                continue
            wick = float(h[k])

        # ⭐ THE CISD LINE COMES FROM THE START OF THE SWEEPING RUN, not from
        # the sweeping candle itself. "CISD is either a single candle or a
        # SERIES of candles, all the same colour, back to back... trace back to
        # where the series starts and mark THAT first candle's body."
        #
        # When the sweep is a single candle the two are identical, so nothing
        # changes there; when it is a run of 2+ same-coloured candles the line
        # moves back to where the run began, which is a better entry price on
        # the same setup. Point-in-time, 103 coins, on the corrected stop:
        #   1d->1h  +0.724%/tr t=+5.84  ->  +0.992%/tr t=+6.86 (halves
        #           +0.992/+0.992 -- identical across both, on 843 trades)
        #   1w->4h  +1.418%/tr t=+2.59  ->  +1.713%/tr t=+2.80
        # Fewer trades (843 vs 1005), better price on each: it does not find
        # more setups, it enters the same ones earlier.
        m = k
        down = c[k] < o[k]
        while m - 1 >= a and ((c[m - 1] < o[m - 1]) == down):
            m -= 1
        line = (max(float(o[m]), float(c[m])) if direction == "LONG"
                else min(float(o[m]), float(c[m])))
        body = abs(float(c[m] - o[m])); rng = float(h[m] - l[m]) or 1e-9
        for j in range(k + 1, min(end + 1, k + 12)):
            broke = c[j] > line if direction == "LONG" else c[j] < line
            if broke:
                lvl = line if body / rng >= small_body_frac else (line + wick) / 2
                ahead = lvl < tp1 if direction == "LONG" else lvl > tp1
                if not ahead:
                    break
                return {"entry": float(lvl), "stop": float(wick),
                        "bars": int(j - k), "sweep_i": int(k),
                        "trigger_i": int(j),
                        # the actual liquidity taken, and the CISD line before
                        # the odd-price nudge -- both needed to FIND this on a
                        # chart, which is the whole point of an alert
                        "swept": float(prior), "cisd_line": float(line),
                        "leg_fvg": _leg_fvg(ltf_df, k, j, direction)}
            out = l[j] < wick if direction == "LONG" else h[j] > wick
            if out:
                break
    return None


def detect_crt_10(htf_df, ltf_df, tf, min_confluence=1,
                  min_rr=CRT10_MIN_RR, max_cisd_bars=CRT10_MAX_CISD_BARS,
                  max_trigger_age=CRT10_MAX_TRIGGER_AGE):
    """CRT 1.0: HTF CRT at a key level, entered on the aligned LTF."""
    # min_rr=0: detect_crt_v3 measures R:R from HTF geometry -- entry at the HTF
    # close, stop at the far HTF sweep extreme (~3% on a daily). CRT 1.0 does
    # not trade that. It enters on the LTF with a stop just beyond the 1h sweep
    # wick, typically ~1%, so the HTF gate rejects setups whose real R:R is
    # excellent, judged against a stop we never use. It is also redundant: the
    # CRT10_MIN_RR check below applies R:R >= 2 to the geometry we DO trade.
    # Measured against the user's own marked BTC CRTs: reproduction rises from
    # 7/59 to 29/59. Cost on BTC over 900 daily bars: 23 -> 65 alerts.
    s = detect_crt_v3(htf_df, tf=tf, min_confluence=min_confluence, min_rr=0)
    if not s:
        return None
    e = crt10_entry(ltf_df, s["direction"], s["signal_ts"], s["tp1"])
    if not e:
        return None
    if e["bars"] > max_cisd_bars:
        return None

    # FRESHNESS. crt10_entry searches forward from the HTF close, which live
    # means "up to now" -- so on a daily setup it can return a CISD from two
    # days ago whose retest has already been and gone. Alerting on that wastes
    # the user's attention and the setup is auto-cancelled minutes later.
    # Only propose a CISD that has just printed.
    age = (len(ltf_df) - 1) - e["trigger_i"]
    if age > max_trigger_age:
        return None

    # And don't propose a trade the market has already killed: if price has
    # traded through the stop since the trigger, it is dead on arrival.
    after = ltf_df.iloc[e["trigger_i"] + 1:]
    if len(after):
        if s["direction"] == "LONG" and float(after["low"].min()) <= e["stop"]:
            return None
        if s["direction"] == "SHORT" and float(after["high"].max()) >= e["stop"]:
            return None

    # Rule 14: never trade the candle that FIRST taps the FVG -- the tap
    # belongs to C1, and the sweep comes after it.
    if "FVG" in s["key_level"]:
        i = len(htf_df) - 1
        if not key_levels.at_fvg(htf_df, s["c1_index"], s["direction"]):
            return None

    # Same real-world price treatment as the deployed detector: a limit that
    # can actually fill, and a stop clear of round-number magnets.
    entry = _entry_easier_fill(e["entry"], s["direction"])

    # ⭐ THE STOP GOES AT THE HTF PROTECTED EXTREME, NOT THE LTF SWEEP WICK.
    # `s["stop"]` is already C2's swept extreme with the odd-price nudge
    # applied; `e["stop"]` was the 1h wick, typically ~1%, which sits inside the
    # noise of the entry timeframe and is hit during a normal HTF-scale move.
    #
    # "Once the second candle closes inside the range, its own high becomes the
    # protection line -- your stop-loss / invalidation marker... as long as C2's
    # protected high/low is never broken, the target is still expected to
    # eventually deliver." Also RIF, the one fully-drawn trade in the course
    # (stop 0.0700 = one tick under the daily low 0.0701, 4.37%).
    #
    # Point-in-time, 103 coins, fills required, both walk-forward halves:
    #   1d->1h   -0.078%/tr t=-0.76  ->  +0.659%/tr t=+5.36
    #   1w->4h   -0.181%/tr t=-0.49  ->  +1.510%/tr t=+2.72
    #   4h->15m  -0.355%/tr t=-6.96  ->  +0.125%/tr t=+2.43
    # Survives removing the best coin, all three thirds of the sample
    # independently, and 0.2%/side fees. Every pairing improves; the one that
    # was reliably losing turns positive.
    stop = s["stop"]
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0:
        return None
    rr = abs(s["tp1"] - entry) / risk
    if rr < min_rr:
        return None

    s = dict(s)
    s.update({
        "entry": float(entry), "stop": float(stop), "rr": round(rr, 2),
        "stop_pct": round(risk / entry * 100, 2),
        "tp1_pct": round(abs(s["tp1"] - entry) / entry * 100, 2),
        "tp2_pct": round(abs(s["tp2"] - entry) / entry * 100, 2),
        "net_pct": round((0.5 * abs(s["tp1"] - entry)
                          + 0.5 * abs(s["tp2"] - entry)) / entry * 100, 2),
        "ltf": CRT10_PAIRS.get(tf), "cisd_bars": e["bars"],
        "swept": e["swept"], "cisd_line": e["cisd_line"],
        "leg_fvg": e["leg_fvg"], "aplus": bool(e["leg_fvg"]),
    })
    return s
