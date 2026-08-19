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


# How far the swept level may sit from the wick that swept it, as a multiple of
# the recent MEDIAN CANDLE RANGE. A multiple, not a fixed percentage, because a
# weekly candle is several times the size of a daily one — the same per-
# timeframe mistake already made twice, with the CISD window and the trigger
# age. Measured on the Round 2 window: a flat 5% cost two of the user's own
# marked setups (recall 6/7 -> 4/7) while 1.0x median range keeps all of them
# AND removes more junk. See at_old_high_low.
OLDHL_MAX_GAP_MULT = 1.0

# How many times price may have traded THROUGH an old high/low before it stops
# counting as a level. None = unlimited (today's behaviour).
#
# Source backing: part1 @1:14:02 -- "if the FVG is closed [filled], then we will
# go to the old highs and lows" -- a level that has been used is no longer the
# level. Their own written rule says the same for FVGs ("once an FVG is tapped
# it is USED UP"), which we already implement as a first-touch rule. The
# untested extension is the same logic for old highs and lows.
# Feature scan, n=30: levels the user rejected had been touched 4.22 times vs
# 2.25 for the ones they accepted (t=-1.79 -- a lead, not a finding).
OLDHL_MAX_TOUCHES = None

# The smallest gap that counts as an FVG, as a fraction of the MEDIAN CANDLE
# RANGE. A multiple, not a percentage of price, so it scales per timeframe.
#
# Without it we credited a 41,479-41,499 gap on BTC daily -- 20 dollars, 0.049%
# of price -- and reported it as the setup's key level. Marking the same chart
# from the definitions alone, the user said "I cannot identify a valid key
# level". Genuine gaps in the same window measured 1.28-5.41% of price.
# CALIBRATED AGAINST THE TWO GAPS THEY JUDGED THEMSELVES, on the same BTC daily
# chart, which bracket the answer:
#   the gap they MARKED as valid   86 pts  0.223% of price  0.058x median range
#   the gap CIA invented (junk)    20 pts  0.050% of price  0.010x median range
# 0.03 sits between them. My first attempt at 0.10 was too high and killed the
# one they had marked -- caught only because their marking was the yardstick.
MIN_FVG_RANGE_MULT = 0.03

# An old high/low only counts if it is still INTACT -- nothing between the swing
# and the setup has already traded beyond it.
#
# Rejected once as "too strict" (18 -> 1 on BTC daily) but that was BEFORE the
# proximity rule landed; on top of proximity it is far cheaper. And the user's
# own marking gives it a concrete case: on ETH 2023-02-17 we named the 2 Feb
# high of 1714.68 while 16 Feb had ALREADY traded to 1742.97 straight through
# it. The genuine untaken high was 1742.97 and price never reached it. Their
# verdict, from the definitions alone: "no key level here -- the previous high
# was not taken." Same mechanism as OLDHL_MAX_TOUCHES and as the source's "if
# the FVG is closed, then we will go to the old highs and lows".
#
# ⚠️ MEASURED AND TURNED OFF. It fixes the two setups they said had no level,
# and it destroys ten they said to TAKE -- including four where they said "key
# levels are fine" in as many words (ADA 1w 2024-04-22, BNB 1w 2025-05-05,
# LINK 1d 2023-12-02, DOGE 1d 2023-09-10). Turning it off restores 7 of 8;
# off AND with the coarse swing lookback, 8 of 8. `journal.py check` caught it:
# coverage 81 -> 56 with 10 lost TAKEs.
#
# So their own labels CONFLICT here: the rule that explains their "no key level"
# calls contradicts their "key levels are fine" calls. Not resolvable from the
# data we have. Left off, one flag away.
OLDHL_REQUIRE_INTACT = False


def _min_gap(df, i, mult=None):
    """Smallest acceptable FVG height at bar i, in price units."""
    mult = MIN_FVG_RANGE_MULT if mult is None else mult
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    a = max(0, i - 20)
    if i <= a:
        return 0.0
    r = sorted(float(h[k] - l[k]) for k in range(a, i))
    return mult * r[len(r) // 2]


def at_old_high_low(df, i, direction, lookback=40, swing_lb=2, swings=None,
                    max_gap_mult=OLDHL_MAX_GAP_MULT, c1_index=None):
    """The setup swept a genuine prior SWING high (short) / low (long) — an old
    level real traders watch — not just any recent candle. Returns
    {level, type} (TWS/TBS) or None.

    TWO THINGS THIS USED TO GET WRONG, both found in the Round 2 chart review
    (research/crt-manual/_LABELS.md):

    1. NO PROXIMITY CHECK. It took the nearest surviving swing level on the far
       side of the wick and called it swept, however far away that was. On BTC
       it reported the 1 May 2022 low of 37,386 as "the old low taken" on SEVEN
       consecutive setups over six weeks while price fell to 26,700 — the last
       of them naming a level 38% away. A level the candle never went near is
       not a level the candle swept. `max_gap_mult` is the same idea as
       `crt10_entry`'s `max_level_gap`, one layer up.

    2. C1's OWN EXTREME COUNTED AS AN "OLD" LEVEL. On a CRT the sweep IS of
       C1's high/low, so returning that as the qualifying old level is circular
       — every CRT would qualify itself. Pass `c1_index` to exclude it.

    Base rate on BTC daily before: 51.9% of all bar-directions, i.e. noise.
    """
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    highs, lows = _swings(df, i, swing_lb, swings)
    short = direction == "SHORT"
    wick = float(h[i]) if short else float(l[i])
    if wick <= 0:
        return None

    # "near" in units of this timeframe's own candles, not a fixed %.
    a = max(0, i - 20)
    if i <= a:
        return None
    ranges = sorted(float(h[k] - l[k]) for k in range(a, i))
    near = max_gap_mult * ranges[len(ranges) // 2]

    def _intact(idx, p):
        if not OLDHL_REQUIRE_INTACT or idx + 1 >= i:
            return True
        seg = h[idx + 1:i] if short else l[idx + 1:i]
        return (float(seg.max()) <= p) if short else (float(seg.min()) >= p)

    src = highs if short else lows
    cands = [p for (idx, p) in src
             if i - lookback <= idx <= i - swing_lb
             and idx != c1_index
             and (p < h[i] if short else p > l[i])
             and abs(p - wick) <= near
             and _intact(idx, p)]
    if not cands:
        return None
    lvl = max(cands) if short else min(cands)     # the nearest one the wick took

    # A level that has already been USED is no longer a level -- the resting
    # orders behind it have been consumed. See OLDHL_MAX_TOUCHES.
    if OLDHL_MAX_TOUCHES is not None:
        idx = [k for (k, p) in src if abs(p - lvl) < 1e-12]
        if idx:
            k0 = idx[-1]
            touches = sum(1 for m in range(k0 + 1, i)
                          if float(h[m]) >= lvl >= float(l[m]))
            if touches > OLDHL_MAX_TOUCHES:
                return None
    typ = ("TBS" if (max(o[i], c[i]) > lvl if short else min(o[i], c[i]) < lvl)
           else "TWS")
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
            if top - bottom < _min_gap(df, i):                  # not a gap
                continue
            filled = l[k + 1:i].min() <= bottom if i > k + 1 else False
            if not filled and not _tapped_before(h, l, k, i, bottom, top)                     and not (hi_i < bottom or lo_i > top):
                return {"bottom": bottom, "top": top}
        if direction == "SHORT" and l[a] > h[k]:                # bearish FVG
            bottom, top = float(h[k]), float(l[a])
            if top - bottom < _min_gap(df, i):                  # not a gap
                continue
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
        if top - bottom < _min_gap(df, i):                      # not a gap
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
    if "oldhl" in types and at_old_high_low(df, i, direction, swings=swings,
                                            c1_index=c1_index):
        labels.append("old high/low")
    # The FVG belongs to C1, not to the sweep candle: "the first candle will
    # tap the fair value gap -- so this is our CRT candle. Now we wait for the
    # second candle... sweep it high or low, and close it in the range"
    # [part1_foundations @36:48-37:07]. detect_crt_10 already checked C1; the
    # LABEL was still being computed on the sweep bar, so alerts disagreed with
    # the rule and with the user's own reading.
    #
    # AND an FVG sitting just BEYOND the swept level counts too. Their own
    # written rule: "the FVG bonus is ADJACENCY -- a gap sitting right above the
    # swept high / below the swept low." We already detected that as the A+ flag
    # but never let it QUALIFY a setup, so BTC 2022-05-01 qualified on an order
    # block -- a level he says he does not trade -- while a real bearish FVG at
    # 38,795-38,881 sat right above C1's high and C2 poked straight into it.
    # The user marked that FVG unprompted from the definitions alone; it is the
    # third time the same miss has come up (Round 2 #6 and #14).
    if "fvg" in types:
        j = i if c1_index is None else c1_index
        lvl = (float(df["high"].to_numpy()[j]) if direction == "SHORT"
               else float(df["low"].to_numpy()[j]))
        if at_fvg(df, j, direction) or fvg_beside_level(df, i, direction, lvl):
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
