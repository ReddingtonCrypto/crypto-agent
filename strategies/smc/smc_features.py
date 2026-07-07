"""SMC Stage 3+4 — objective (best-effort) detection of Smart-Money concepts.

These are discretionary ideas, so the rules below are deliberate, measurable
approximations based on common ICT/SMC definitions:

- Equal highs/lows : last two swing highs (or lows) within a small tolerance =
                     resting liquidity.
- Liquidity sweep  : latest candle wicks beyond the most recent swing high/low
                     but CLOSES back inside (failed break = stop grab).
- Stop hunt        : a liquidity sweep that occurred at an equal-highs/lows level.
- Session sweep    : latest candle sweeps the PREVIOUS UTC day's high/low and
                     closes back inside (proxy for session-liquidity sweep).
- Fair Value Gap   : 3-candle imbalance; bullish if candle1.high < candle3.low,
                     bearish if candle1.low > candle3.high.
- Order block      : last opposite-colour candle before a strong displacement
                     candle (body > 1.5x recent average body).
- Breaker block    : an order block that FAILED (price closed through it).
- Mitigation block : price has returned INTO an order block that still holds.
- MSS              : a liquidity sweep followed by a close beyond the opposite
                     pivot (confirmed market-structure shift).

`analyze(df)` returns {tags, bull, bear, bias}: human-readable tags plus a
simple bullish/bearish lean used to nudge signal confidence.
"""

import pandas as pd

from strategies.smc.market_structure import find_swings


def equal_levels(highs, lows, tol=0.001):
    eqh = len(highs) >= 2 and abs(highs[-1][1] - highs[-2][1]) <= tol * highs[-1][1]
    eql = len(lows) >= 2 and abs(lows[-1][1] - lows[-2][1]) <= tol * lows[-1][1]
    return bool(eqh), bool(eql)


def liquidity_sweep(df, highs, lows):
    last = df.iloc[-1]
    out = {"buyside": False, "sellside": False}
    if highs:
        lvl = highs[-1][1]
        if last["high"] > lvl and last["close"] < lvl:
            out["buyside"] = True   # grabbed liquidity above a high (bearish)
    if lows:
        lvl = lows[-1][1]
        if last["low"] < lvl and last["close"] > lvl:
            out["sellside"] = True  # grabbed liquidity below a low (bullish)
    return out


def session_sweep(df):
    if "timestamp" not in df.columns:
        return None
    ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    day = ts.dt.date
    last_day = day.iloc[-1]
    prev = day < last_day
    if not prev.any():
        return None
    prev_date = day[prev].iloc[-1]
    mask = day == prev_date
    ph = df.loc[mask, "high"].max()
    pl = df.loc[mask, "low"].min()
    last = df.iloc[-1]
    if last["high"] > ph and last["close"] < ph:
        return "buyside"
    if last["low"] < pl and last["close"] > pl:
        return "sellside"
    return None


def fair_value_gap(df, lookback=15):
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    for c in range(n - 1, max(n - 1 - lookback, 2) - 1, -1):
        a = c - 2
        if a < 0:
            break
        if highs[a] < lows[c]:
            return "BULLISH"
        if lows[a] > highs[c]:
            return "BEARISH"
    return None


def fvg_zone(df, lookback=15):
    """Like fair_value_gap but returns the PRICE LEVELS of the most recent gap,
    for retracement entries: {"dir", "top", "bottom"} or None.

    Bullish gap = between candle a.high (bottom) and candle c.low (top), with
    a.high < c.low. Bearish gap = between c.high (bottom) and a.low (top).
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    for c in range(n - 1, max(n - 1 - lookback, 2) - 1, -1):
        a = c - 2
        if a < 0:
            break
        if highs[a] < lows[c]:
            return {"dir": "BULLISH", "bottom": float(highs[a]), "top": float(lows[c])}
        if lows[a] > highs[c]:
            return {"dir": "BEARISH", "bottom": float(highs[c]), "top": float(lows[a])}
    return None


def order_block(df, lookback=20):
    body = (df["close"] - df["open"]).abs()
    avg = body.tail(lookback).mean()
    if not avg or pd.isna(avg):
        return None
    n = len(df)
    for i in range(n - 1, max(n - lookback, 1) - 1, -1):
        c = df.iloc[i]
        if abs(c["close"] - c["open"]) > 1.5 * avg:
            impulse_up = c["close"] > c["open"]
            for j in range(i - 1, max(i - 6, -1), -1):
                prev = df.iloc[j]
                down = prev["close"] < prev["open"]
                if impulse_up and down:
                    return {"dir": "BULLISH", "top": float(prev["high"]), "bottom": float(prev["low"])}
                if (not impulse_up) and (not down):
                    return {"dir": "BEARISH", "top": float(prev["high"]), "bottom": float(prev["low"])}
            break
    return None


def analyze(df):
    tags = []
    bull = 0
    bear = 0

    highs, lows = find_swings(df, lookback=2)
    last_close = float(df["close"].iloc[-1])

    eqh, eql = equal_levels(highs, lows)
    if eqh:
        tags.append("Equal highs (liquidity above)")
    if eql:
        tags.append("Equal lows (liquidity below)")

    sweep = liquidity_sweep(df, highs, lows)
    if sweep["sellside"]:
        tags.append("Sell-side sweep"); bull += 1
    if sweep["buyside"]:
        tags.append("Buy-side sweep"); bear += 1
    if sweep["sellside"] and eql:
        tags.append("Stop hunt below equal lows")
    if sweep["buyside"] and eqh:
        tags.append("Stop hunt above equal highs")

    ss = session_sweep(df)
    if ss == "sellside":
        tags.append("Prev-day low sweep"); bull += 1
    elif ss == "buyside":
        tags.append("Prev-day high sweep"); bear += 1

    fvg = fair_value_gap(df)
    if fvg == "BULLISH":
        tags.append("Bullish FVG"); bull += 1
    elif fvg == "BEARISH":
        tags.append("Bearish FVG"); bear += 1

    ob = order_block(df)
    if ob:
        if ob["dir"] == "BULLISH":
            tags.append("Bullish order block"); bull += 1
        else:
            tags.append("Bearish order block"); bear += 1
        # Mitigation = price back inside an OB that still holds.
        if ob["bottom"] <= last_close <= ob["top"]:
            tags.append("OB retest (mitigation)")
        # Breaker = OB failed (price closed through its far side).
        if ob["dir"] == "BULLISH" and last_close < ob["bottom"]:
            tags.append("Breaker (bullish OB failed)"); bear += 1
        if ob["dir"] == "BEARISH" and last_close > ob["top"]:
            tags.append("Breaker (bearish OB failed)"); bull += 1

    # MSS = sweep + close beyond the opposite pivot (confirmed shift).
    if sweep["sellside"] and highs and last_close > highs[-1][1]:
        tags.append("MSS bullish"); bull += 2
    if sweep["buyside"] and lows and last_close < lows[-1][1]:
        tags.append("MSS bearish"); bear += 2

    bias = "BULLISH" if bull > bear else "BEARISH" if bear > bull else "NEUTRAL"
    return {"tags": tags, "bull": bull, "bear": bear, "bias": bias}


if __name__ == "__main__":
    import ccxt
    ex = ccxt.binanceus()
    for coin in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        bars = ex.fetch_ohlcv(coin, timeframe="1h", limit=200)
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        print(coin, "->", analyze(df))
