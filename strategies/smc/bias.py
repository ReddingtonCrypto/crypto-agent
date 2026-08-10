"""ICT Daily Bias + Draw-on-Liquidity engine — the ANTICIPATORY core.

Answers two questions BEFORE the expansion happens:
  1. daily_bias(df)         -> which way is price likely to deliver? (LONG/SHORT)
  2. draw_on_liquidity(...)  -> where is it being drawn to? (the target pool)

Bias is a vote of mechanical ICT rules (net sign = the bias); each rule reports
its reason so a human can validate the call:

  R1 Order flow structure : HH+HL -> +1 bull,  LH+LL -> -1 bear (daily swings).
  R2 Close vs midpoint    : last candle closed in its upper/lower half -> +/-1.
  R3 Sweep + reclaim      : swept a prior swing HIGH then closed back below -> -1
                            (bearish next); swept a swing LOW + closed above -> +1.
  R4 Weekly confirm       : swept last week's low + closed above -> +1; swept
                            last week's high + closed below -> -1.
  R5 Unfilled imbalance   : a still-open bullish FVG the last candle tapped -> +1;
                            a bearish FVG tapped -> -1 (the imbalance to be filled).

Draw on liquidity = the nearest UNTAPPED external pool (swing high above for a
bull bias / swing low below for a bear bias) that price hasn't traded through yet
— the logical destination of the move (the ERL target in ERL->IRL->ERL).

Read-only analytics; no side effects. Reused by strategies + the scanner.
"""

from strategies.smc.market_structure import find_swings
from strategies.smc import key_levels


def _structure(highs, lows):
    """+1 for HH+HL (uptrend), -1 for LH+LL (downtrend), 0 otherwise."""
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]:
            return 1
        if highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]:
            return -1
    return 0


def daily_bias(df, weekly_df=None, swing_lb=3):
    """Return {direction, score, reasons} for the last closed candle of `df`
    (intended: a DAILY frame). `weekly_df` (optional) adds the weekly confirm."""
    if len(df) < 30:
        return {"direction": "NEUTRAL", "score": 0, "reasons": []}
    highs, lows = find_swings(df, lookback=swing_lb)
    h = df["high"].to_numpy(); l = df["low"].to_numpy(); c = df["close"].to_numpy()
    i = len(df) - 1
    score = 0
    reasons = []

    # R1 order-flow structure
    s = _structure(highs, lows)
    if s:
        score += s
        reasons.append("HH+HL" if s > 0 else "LH+LL")

    # R2 close vs the last candle's own midpoint
    mid = (h[i] + l[i]) / 2.0
    if c[i] > mid:
        score += 1; reasons.append("close>midpoint")
    elif c[i] < mid:
        score -= 1; reasons.append("close<midpoint")

    # R3 sweep + reclaim of the most recent prior swing (the next-candle model)
    if highs and highs[-1][0] < i and h[i] > highs[-1][1] and c[i] < highs[-1][1]:
        score -= 1; reasons.append("swept-high+reclaim")
    if lows and lows[-1][0] < i and l[i] < lows[-1][1] and c[i] > lows[-1][1]:
        score += 1; reasons.append("swept-low+reclaim")

    # R4 weekly confirm
    if weekly_df is not None and len(weekly_df) >= 3:
        wh = weekly_df["high"].to_numpy(); wl = weekly_df["low"].to_numpy()
        wc = weekly_df["close"].to_numpy(); j = len(weekly_df) - 1
        if wl[j] < wl[j - 1] and wc[j] > wl[j - 1]:
            score += 1; reasons.append("weekly-swept-low+reclaim")
        if wh[j] > wh[j - 1] and wc[j] < wh[j - 1]:
            score -= 1; reasons.append("weekly-swept-high+reclaim")

    # R5 unfilled imbalance the last candle is reacting to
    if key_levels.at_fvg(df, i, "LONG"):
        score += 1; reasons.append("bullish-FVG")
    elif key_levels.at_fvg(df, i, "SHORT"):
        score -= 1; reasons.append("bearish-FVG")

    direction = "LONG" if score > 0 else ("SHORT" if score < 0 else "NEUTRAL")
    return {"direction": direction, "score": score, "reasons": reasons}


def draw_on_liquidity(df, direction, swing_lb=3, lookback=60):
    """The nearest UNTAPPED external pool in the bias direction — the target.
    LONG -> nearest swing HIGH above price not yet traded through.
    SHORT -> nearest swing LOW below price not yet traded through. None if none."""
    if direction not in ("LONG", "SHORT") or len(df) < swing_lb + 3:
        return None
    highs, lows = find_swings(df, lookback=swing_lb)
    h = df["high"].to_numpy(); l = df["low"].to_numpy()
    i = len(df) - 1
    price = float(df["close"].iloc[i])
    if direction == "LONG":
        cands = sorted(p for (idx, p) in highs
                       if i - lookback <= idx < i and p > price
                       and h[idx + 1:i + 1].max() < p)          # untapped since forming
        return float(cands[0]) if cands else None
    cands = sorted((p for (idx, p) in lows
                    if i - lookback <= idx < i and p < price
                    and l[idx + 1:i + 1].min() > p), reverse=True)
    return float(cands[0]) if cands else None


def bias_and_draw(daily_df, weekly_df=None):
    """Convenience: the bias plus its draw-on-liquidity target in one call."""
    b = daily_bias(daily_df, weekly_df=weekly_df)
    b["draw"] = draw_on_liquidity(daily_df, b["direction"])
    return b
