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
    setup_extreme = l[i] if direction == "LONG" else h[i]
    start = max(2, i - lookback)
    for k in range(i, start - 1, -1):
        a = k - 2
        if a < 0:
            break
        if direction == "LONG" and h[a] < l[k]:                 # bullish FVG
            bottom, top = float(h[a]), float(l[k])
            filled = l[k + 1:i].min() <= bottom if i > k + 1 else False
            if not filled and bottom <= setup_extreme <= top * 1.001:
                return {"bottom": bottom, "top": top}
        if direction == "SHORT" and l[a] > h[k]:                # bearish FVG
            bottom, top = float(h[k]), float(l[a])
            filled = h[k + 1:i].max() >= top if i > k + 1 else False
            if not filled and bottom * 0.999 <= setup_extreme <= top:
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
def at_key_level(df, i, direction, types=("oldhl", "fvg", "rejblock"), swings=None):
    if "oldhl" in types and at_old_high_low(df, i, direction, swings=swings):
        return "old high/low sweep"
    if "fvg" in types and at_fvg(df, i, direction):
        return "unfilled FVG"
    if "rejblock" in types and at_rejection_block(df, i, direction, swings=swings):
        return "rejection block"
    return None


def count_key_levels(df, i, direction, types=("oldhl", "fvg", "rejblock"), swings=None):
    """How many of the enabled key levels are present at once — the 'confluence'
    that separates an A+ setup (several stacking) from a marginal one (just one).
    Returns (count, labels)."""
    labels = []
    if "oldhl" in types and at_old_high_low(df, i, direction, swings=swings):
        labels.append("old high/low")
    if "fvg" in types and at_fvg(df, i, direction):
        labels.append("FVG")
    if "rejblock" in types and at_rejection_block(df, i, direction, swings=swings):
        labels.append("rejection block")
    return len(labels), labels
