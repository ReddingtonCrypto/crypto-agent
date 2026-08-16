"""CRT key levels + liquidity sweeps — careful, separate, testable detectors.

The whole strategy hinges on a LIQUIDITY SWEEP: price runs an old level to trap
breakout traders, then reverses. Every key level is a place that sweep is more
likely to reverse from. Each detector here is independent so it can be tested on
its own (see crt_v2.py --kl=oldhl,fvg,rejblock).

Sweep types (per the strategy's "Turtle Soup"):
  * TWS — Turtle Wick Sweep: only the WICK pokes beyond the level, body closes
    back inside. Lower probability.
  * TBS — Turtle Body Sweep: the BODY closes beyond the level, then price
    reverses back through. Higher probability (A+).

All functions take a raw OHLCV DataFrame (columns open/high/low/close, a numeric
index) and the bar index `i` being evaluated (the CRT's C2), plus `direction`
("LONG" or "SHORT"). They return a dict describing the level, or None.
"""

from strategies.smc.market_structure import find_swings


# --------------------------------------------------------------------------- #
#  Liquidity sweep — the core mechanic every key level depends on.
# --------------------------------------------------------------------------- #
def sweep_of_recent_extreme(df, i, direction, lookback=20):
    """Did bar i sweep the recent `lookback`-bar extreme AND close back inside
    (a valid Turtle-Soup sweep-and-reject)? Returns {level, type} or None."""
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    a = max(0, i - lookback)
    if i <= a:
        return None
    if direction == "SHORT":
        lvl = float(h[a:i].max())
        if h[i] > lvl:                                 # wick took the level
            # TBS = body closed BEYOND the level (reversal comes later);
            # TWS = only the wick poked beyond, body stayed below.
            typ = "TBS" if max(o[i], c[i]) > lvl else "TWS"
            return {"level": lvl, "type": typ}
    else:
        lvl = float(l[a:i].min())
        if l[i] < lvl:
            typ = "TBS" if min(o[i], c[i]) < lvl else "TWS"
            return {"level": lvl, "type": typ}
    return None


# --------------------------------------------------------------------------- #
#  Key level 1 — OLD HIGH / OLD LOW (a swept prior SWING level).
# --------------------------------------------------------------------------- #
def _swings(df, i, swing_lb, swings):
    """Precomputed (highs, lows) when given (fast backtest), else compute up to
    bar i (live). Callers must still filter idx <= i - swing_lb (no look-ahead)."""
    if swings is not None:
        return swings
    return find_swings(df.iloc[:i + 1], lookback=swing_lb)


def at_old_high_low(df, i, direction, lookback=40, swing_lb=2, swings=None):
    """The setup swept a genuine prior SWING high (short) / low (long) — an old
    level real traders watch — not just any recent candle. Returns
    {level, type} (TWS/TBS) or None."""
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    highs, lows = _swings(df, i, swing_lb, swings)
    if direction == "SHORT":
        cands = [p for (idx, p) in highs if idx <= i - swing_lb and idx >= i - lookback and p < h[i]]
        if not cands:
            return None
        lvl = max(cands)                              # nearest old swing high the wick took out
        typ = "TBS" if max(o[i], c[i]) > lvl else "TWS"
        return {"level": float(lvl), "type": typ}
    else:
        cands = [p for (idx, p) in lows if idx <= i - swing_lb and idx >= i - lookback and p > l[i]]
        if not cands:
            return None
        lvl = min(cands)
        typ = "TBS" if min(o[i], c[i]) < lvl else "TWS"
        return {"level": float(lvl), "type": typ}


# --------------------------------------------------------------------------- #
#  Key level 2 — FAIR VALUE GAP (unfilled 3-candle imbalance).
# --------------------------------------------------------------------------- #
def at_fvg(df, i, direction, lookback=15):
    """A matching-direction, still-UNFILLED 3-candle FVG that the setup is
    reacting to (price has come back to tap it). Bullish gap = high[a] < low[c]
    (demand); bearish = low[a] > high[c] (supply). 'Unfilled' = price hasn't
    fully traded back through the gap between when it formed and bar i.
    Returns {top, bottom} or None."""
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    # THE CANDLE MUST *TAP* THE GAP -- its RANGE has to reach into the zone.
    # Testing only the candle's extreme (the old behaviour) missed the case
    # where the range straddles the gap entirely, which is the commonest way a
    # big candle interacts with one. Verified on BTC 2021-11-16: C1's range
    # spanned the 61,560-63,274 gap the user had marked, and we returned None
    # because its low sat below the zone.
    lo_i, hi_i = float(l[i]), float(h[i])
    start = max(2, i - lookback)
    for k in range(i, start - 1, -1):
        a = k - 2
        if a < 0:
            break
        if direction == "LONG" and h[a] < l[k]:                 # bullish FVG
            bottom, top = float(h[a]), float(l[k])
            filled = l[k + 1:i].min() <= bottom if i > k + 1 else False
            if not filled and not _tapped_before(h, l, k, i, bottom, top)                     and not (hi_i < bottom or lo_i > top):
                return {"bottom": bottom, "top": top}
        if direction == "SHORT" and l[a] > h[k]:                # bearish FVG
            bottom, top = float(h[k]), float(l[a])
            filled = h[k + 1:i].max() >= top if i > k + 1 else False
            if not filled and not _tapped_before(h, l, k, i, bottom, top)                     and not (hi_i < bottom or lo_i > top):
                return {"bottom": bottom, "top": top}
    return None


def _tapped_before(h, l, k, i, bottom, top):
    """Has anything already reached into this gap before bar i?

    "Once an FVG is tapped, it is USED UP -- don't use the same FVG as a key
    level twice." Our old test was only whether the gap had been FILLED (traded
    fully through). A gap that price dipped into and respected is still
    unfilled, but it is spent: the imbalance has been rebalanced once and it is
    no longer virgin. Bar i must therefore be the FIRST touch.
    """
    for m in range(k + 1, i):
        if not (h[m] < bottom or l[m] > top):
            return True
    return False


def fvg_beside_level(df, i, direction, level, lookback=15, gap_pct=0.02):
    """An unfilled FVG sitting just BEYOND the swept level -- the A+ stack.

    "Bonus: if there's also an FVG sitting right above the swept high (bearish)
    or right below the swept low (bullish), that stacks as extra confluence."
    This is ADJACENCY, not containment: the gap sits past the level, in the
    direction price just came from, so the reversal has an imbalance to run
    into. Distinct from at_fvg, which asks whether the candle TAPS a gap.
    """
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    start = max(2, i - lookback)
    span = abs(level) * gap_pct
    for k in range(i, start - 1, -1):
        a = k - 2
        if a < 0:
            break
        if h[a] < l[k]:
            bottom, top = float(h[a]), float(l[k])
        elif l[a] > h[k]:
            bottom, top = float(h[k]), float(l[a])
        else:
            continue
        if direction == "SHORT" and level < bottom <= level + span:
            return {"bottom": bottom, "top": top}
        if direction == "LONG" and level - span <= top < level:
            return {"bottom": bottom, "top": top}
    return None


# --------------------------------------------------------------------------- #
#  Key level 3 — REJECTION BLOCK (a FAILED CISD).
# --------------------------------------------------------------------------- #
#  CISD (Change in State of Delivery): a same-colour candle RUN that sweeps
#  liquidity; the line is the FIRST candle-of-the-run's body edge; it CONFIRMS
#  when an opposite-colour candle closes through the line. If it never closes
#  through (price rejects and holds), that line becomes a REJECTION BLOCK.
#
#   * Failed BEARISH CISD (up-run swept a high, no red close below the line)
#       -> BULLISH rejection block (support)  -> for LONG CRTs.
#   * Failed BULLISH CISD (down-run swept a low, no green close above the line)
#       -> BEARISH rejection block (resistance) -> for SHORT CRTs.
# --------------------------------------------------------------------------- #
def at_rejection_block(df, i, direction, lookback=30, swing_lb=2, tol=0.01, swings=None):
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    highs, lows = _swings(df, i, swing_lb, swings)
    start = max(3, i - lookback)

    if direction == "LONG":
        setup_lvl = l[i]
        # Look for a failed BEARISH CISD: an up (green) run that swept a prior
        # swing high, whose "line" (first green candle's body low) price never
        # closed a red candle below -> that line held as support.
        for j in range(i - 1, start, -1):
            if c[j] <= o[j]:                          # not the end of an up-run
                continue
            # walk back to the start of this green run
            s = j
            while s > start and c[s - 1] > o[s - 1]:
                s -= 1
            run_high = h[s:j + 1].max()
            swept = any(p < run_high and idx < s for (idx, p) in highs
                        if i - lookback <= idx <= i - swing_lb)
            if not swept:
                continue
            line = min(o[s], c[s])                    # first green candle body low
            # failed = no RED candle closed below `line` after the run, up to i
            failed = not any(c[k] < line and c[k] < o[k] for k in range(j + 1, i))
            if failed and abs(setup_lvl - line) / line <= tol:
                return {"level": float(line)}
        return None
    else:
        setup_lvl = h[i]
        for j in range(i - 1, start, -1):
            if c[j] >= o[j]:                          # not the end of a down-run
                continue
            s = j
            while s > start and c[s - 1] < o[s - 1]:
                s -= 1
            run_low = l[s:j + 1].min()
            swept = any(p > run_low and idx < s for (idx, p) in lows
                        if i - lookback <= idx <= i - swing_lb)
            if not swept:
                continue
            line = max(o[s], c[s])                    # first red candle body high
            failed = not any(c[k] > line and c[k] > o[k] for k in range(j + 1, i))
            if failed and abs(setup_lvl - line) / line <= tol:
                return {"level": float(line)}
        return None


# --------------------------------------------------------------------------- #
#  Combined helper — is bar i at ANY enabled key level? Returns the label.
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  Key level 4 — ORDER BLOCK (the last opposite-colour candle before displacement).
# --------------------------------------------------------------------------- #
def at_order_block(df, i, direction, lookback=20, disp_window=3, tol=0.003):
    """A valid ICT Order Block the setup is reacting to.
    Bullish OB (for LONG) = the last DOWN candle before a strong up move: a
    bearish candle at j that price then DISPLACED up through (a later candle
    within `disp_window` CLOSED above its high). The OB zone is [low[j], high[j]];
    the setup qualifies when its swept extreme taps that zone. Bearish OB mirrors.
    Returns {top, bottom} or None."""
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    swept = l[i] if direction == "LONG" else h[i]
    start = max(1, i - lookback)
    for j in range(i - 1, start - 1, -1):
        top, bottom = float(h[j]), float(l[j])
        if direction == "LONG":
            if c[j] >= o[j]:                       # need a DOWN candle
                continue
            kd = next((k for k in range(j + 1, min(j + 1 + disp_window, i + 1))
                       if c[k] > top), None)       # up-displacement CLOSED above it
            if kd is None:
                continue
            # FRESH: price stayed above the OB from the displacement until now, so
            # bar i is the FIRST retest (unmitigated order block).
            if kd + 1 < i and l[kd + 1:i].min() <= top:
                continue
        else:
            if c[j] <= o[j]:                       # need an UP candle
                continue
            kd = next((k for k in range(j + 1, min(j + 1 + disp_window, i + 1))
                       if c[k] < bottom), None)
            if kd is None:
                continue
            if kd + 1 < i and h[kd + 1:i].max() >= bottom:
                continue
        if bottom * (1 - tol) <= swept <= top * (1 + tol):
            return {"top": top, "bottom": bottom}
    return None


# --------------------------------------------------------------------------- #
#  Key level 5 — EQUAL HIGHS / LOWS (engineered liquidity — the strongest pool).
# --------------------------------------------------------------------------- #
def at_equal_liquidity(df, i, direction, swing_lb=3, lookback=40, tol=0.005, swings=None):
    """The sweep took out EQUAL highs/lows — 2+ prior swings clustered within
    `tol` of each other that the current bar's wick just exceeded. This is ICT
    'engineered liquidity' (equal highs = stacked short stops; equal lows =
    stacked long stops) — the densest, highest-quality liquidity grab. Returns
    {level, count} or None."""
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    highs, lows = _swings(df, i, swing_lb, swings)
    if direction == "SHORT":
        cands = sorted((p for (idx, p) in highs if i - lookback <= idx < i and p < h[i]),
                       reverse=True)
        if cands:
            top = cands[0]
            cluster = [p for p in cands if top > 0 and (top - p) / top <= tol]
            if len(cluster) >= 2:
                return {"level": float(top), "count": len(cluster)}
    else:
        cands = sorted(p for (idx, p) in lows if i - lookback <= idx < i and p > l[i])
        if cands:
            bot = cands[0]
            cluster = [p for p in cands if bot > 0 and (p - bot) / bot <= tol]
            if len(cluster) >= 2:
                return {"level": float(bot), "count": len(cluster)}
    return None


# --------------------------------------------------------------------------- #
#  Quality gate — DISPLACEMENT (the energetic candle that makes a break a real MSS).
# --------------------------------------------------------------------------- #
def is_displacement(df, i, direction, lookback=20, body_mult=1.5, body_frac=0.5):
    """Bar i is a DISPLACEMENT in `direction` — a large, fast, one-sided candle
    (what separates a real Market Structure Shift from a routine wiggle):
      * its body is >= body_mult x the recent average candle range, AND
      * the body dominates the candle (>= body_frac of its high-low range), AND
      * it closes in the trade direction (bullish for LONG, bearish for SHORT).
    """
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    rng = h[i] - l[i]
    if rng <= 0 or i < lookback:
        return False
    avg = (h[i - lookback:i] - l[i - lookback:i]).mean()
    if avg <= 0:
        return False
    body = abs(c[i] - o[i])
    if body < body_mult * avg or body < body_frac * rng:
        return False
    return c[i] > o[i] if direction == "LONG" else c[i] < o[i]


def at_key_level(df, i, direction, types=("oldhl", "fvg", "rejblock", "ob", "eqhl", "disp"), swings=None):
    if "oldhl" in types and at_old_high_low(df, i, direction, swings=swings):
        return "old high/low sweep"
    if "fvg" in types and at_fvg(df, i, direction):
        return "unfilled FVG"
    if "rejblock" in types and at_rejection_block(df, i, direction, swings=swings):
        return "rejection block"
    if "ob" in types and at_order_block(df, i, direction):
        return "order block"
    if "eqhl" in types and at_equal_liquidity(df, i, direction, swings=swings):
        return "equal highs/lows"
    if "disp" in types and is_displacement(df, i, direction):
        return "displacement"
    return None


# The four the mentor names as his own, part1_foundations @1:24:19:
# "personally, I use the old highs, old lows and FVG and rejection blocks. I use
# the rejection blocks and FVG best." Order block he confirms IS a valid key
# level (@1:23:09) but says he does not trade it; equal highs/lows and
# displacement came from our ICT work, not from him.
#
# NOTHING is removed on the strength of this -- the others are kept and still
# count toward confluence, because the mentor and the group do use them at
# times. This only marks which is which, so the alerts teach the distinction
# and so we can later measure whether the core levels behave differently.
MENTOR_CORE = ("old high/low", "FVG", "rejection block")


def describe_levels(labels):
    """Render key-level labels, marking the mentor's own from the rest."""
    core = [x for x in labels if x in MENTOR_CORE]
    extra = [x for x in labels if x not in MENTOR_CORE]
    if core and extra:
        return f"{' + '.join(core)} (core) · {' + '.join(extra)} (secondary)"
    if core:
        return f"{' + '.join(core)} (core)"
    if extra:
        return f"{' + '.join(extra)} (secondary only)"
    return "none"


def count_key_levels(df, i, direction,
                     types=("oldhl", "fvg", "rejblock", "ob", "eqhl", "disp"),
                     swings=None, c1_index=None):
    """How many of the enabled key levels are present at once — the 'confluence'
    that separates an A+ setup (several stacking) from a marginal one (just one).
    Returns (count, labels)."""
    labels = []
    if "oldhl" in types and at_old_high_low(df, i, direction, swings=swings):
        labels.append("old high/low")
    # The FVG belongs to C1, not to the sweep candle: "the first candle will
    # tap the fair value gap -- so this is our CRT candle. Now we wait for the
    # second candle... sweep it high or low, and close it in the range"
    # [part1_foundations @36:48-37:07]. detect_crt_10 already checked C1; the
    # LABEL was still being computed on the sweep bar, so alerts disagreed with
    # the rule and with the user's own reading.
    if "fvg" in types and at_fvg(df, i if c1_index is None else c1_index,
                                 direction):
        labels.append("FVG")
    if "rejblock" in types and at_rejection_block(df, i, direction, swings=swings):
        labels.append("rejection block")
    if "ob" in types and at_order_block(df, i, direction):
        labels.append("order block")
    if "eqhl" in types and at_equal_liquidity(df, i, direction, swings=swings):
        labels.append("equal highs/lows")
    if "disp" in types and is_displacement(df, i, direction):
        labels.append("displacement")
    return len(labels), labels
