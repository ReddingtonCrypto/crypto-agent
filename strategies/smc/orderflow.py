"""Order-flow reads computed from candles (free — no trade-tick data needed):

- CVD proxy : Cumulative Volume Delta approximated per candle by where the
              close sits in the range (close near high = buying, near low =
              selling), weighted by volume. Rising = buyers in control.
- CISD      : Change in State of Delivery — price closing back through the
              ORIGIN of the last run of same-colour candles signals order flow
              has flipped (bullish/bearish).
- volume_rising : latest volume above its recent average (real participation).

These are confirmations to sharpen DIRECTION; they are approximations of the
tick-level versions, but directionally useful.
"""

import numpy as np


def cvd_proxy(df, window=20):
    seg = df.tail(window)
    high = seg["high"].to_numpy(dtype=float)
    low = seg["low"].to_numpy(dtype=float)
    close = seg["close"].to_numpy(dtype=float)
    vol = seg["volume"].to_numpy(dtype=float)

    rng = high - low
    rng[rng == 0] = 1e-9
    clv = ((close - low) - (high - close)) / rng  # -1 (sell) .. +1 (buy)
    delta = vol * clv
    cvd = delta.cumsum()

    if len(cvd) < 4:
        return "NEUTRAL"
    net = cvd[-1] - cvd[0]
    mag = np.abs(delta).sum() or 1.0
    if net > 0.15 * mag:
        return "BULLISH"
    if net < -0.15 * mag:
        return "BEARISH"
    return "NEUTRAL"


def cisd(df, lookback=12):
    seg = df.tail(lookback + 1)
    o = seg["open"].to_numpy(dtype=float)
    c = seg["close"].to_numpy(dtype=float)
    n = len(seg)
    if n < 3:
        return None
    last_close = c[-1]
    j = n - 2  # the candle just before the current one

    def bear(k):
        return c[k] < o[k]

    def bull(k):
        return c[k] > o[k]

    # Bullish CISD: a bearish run, then current close back ABOVE the run's origin open.
    if bear(j):
        start = j
        while start - 1 >= 0 and bear(start - 1):
            start -= 1
        if last_close > o[start]:
            return "BULLISH"

    # Bearish CISD: a bullish run, then current close back BELOW the run's origin open.
    if bull(j):
        start = j
        while start - 1 >= 0 and bull(start - 1):
            start -= 1
        if last_close < o[start]:
            return "BEARISH"

    return None


def volume_rising(df, window=20):
    seg = df.tail(window)
    vol = seg["volume"].to_numpy(dtype=float)
    if len(vol) < 3:
        return False
    return bool(vol[-1] > vol[:-1].mean())
