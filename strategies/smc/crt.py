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
