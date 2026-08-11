"""ICT model strategy — the classic 2022 sequence, as a strict entry trigger:

    1. Liquidity sweep  : price grabs a recent swing high/low (stops).
    2. Market Structure : it then CLOSES beyond the opposite pivot (MSS) — the
       Shift (MSS)         turn is confirmed.
    3. Fair Value Gap   : the impulsive move leaves an FVG in the new direction.

Only when all three line up do we emit a signal. This is a mechanical
approximation of a discretionary concept, so it fires rarely (by design).

Returns {"direction": "LONG"|"SHORT", "swept": level} or None.
"""

import numpy as np

from strategies.smc.market_structure import find_swings
from strategies.smc.smc_features import fair_value_gap


def _pivots(h, l, i, lb, lookback):
    """Pivot highs/lows strictly BEFORE bar i (no lookahead)."""
    hi, lo = [], []
    for k in range(max(lb, i - lookback), i - lb):
        if h[k] == max(h[k - lb:k + lb + 1]):
            hi.append((k, float(h[k])))
        if l[k] == min(l[k - lb:k + lb + 1]):
            lo.append((k, float(l[k])))
    return hi, lo


def detect_ict_source(df, window=12, swing_lb=2, lookback=60, fvg_search=4,
                      max_stop_pct=0.08):
    """The 2022 ICT model as the SOURCE lectures actually specify it.

    The previous `detect_ict` kept the sweep+shift trigger but invented its own
    entry, stop and target. Measured on 1,856 paired historical setups that cost
    -1.02%/trade; the source's geometry on the SAME signals returned +0.65%.
    What the lectures say, and what this implements:

      * entry  : a LIMIT at the near edge of the fair value gap left by the
                 displacement leg -- "place a buy limit order at the low of the
                 premium high of the fair value gap". You wait for the retrace
                 instead of chasing the shift candle's close.
      * stop   : just beyond the candle framing the gap's far edge -- "right at
                 the high, not one tick above it", deliberately tight.
      * target : the nearest prior swing high ABOVE the current close. An entry
                 at a gap is INTERNAL range liquidity, and internal entries run
                 to EXTERNAL liquidity (Model 9's alternation rule). This is the
                 piece that carries the edge: fixed 2R and 4R targets both fail
                 walk-forward, this one is positive in both halves.

    Returns {direction, entry, stop, tp1, tp2, rr, key_level, confluence,
    signal_ts} shaped like detect_crt_scout so the approval flow can reuse it,
    or None. LONG only -- shorts were never validated.
    """
    n = len(df)
    if n < max(lookback + 10, 60):
        return None
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    i = n - 1

    hi, lo = _pivots(h, l, i, swing_lb, lookback)
    if not hi or not lo:
        return None
    swing_high = hi[-1][1]
    swing_low = lo[-1][1]

    # 1. sell-side raid, then a shift that closes above the opposing pivot
    a = max(0, i - window)
    if not (l[a:i + 1].min() < swing_low and c[i] > swing_high):
        return None

    # 2. the fair value gap left by that leg (nearest first)
    gap = None
    for k in range(i, max(i - fvg_search, 2) - 1, -1):
        if h[k - 2] < l[k]:
            gap = {"entry": float(l[k]), "far": float(h[k - 2]),
                   "stop": float(l[k - 2])}
            break
    if gap is None:
        return None

    entry, stop = gap["entry"], gap["stop"]
    risk = entry - stop
    if risk <= 0 or entry <= 0 or risk / entry > max_stop_pct:
        return None

    # 3. target the nearest prior swing high still ABOVE current price
    above = [p for _, p in hi if p > c[i]]
    if not above:
        return None
    tp2 = min(above)
    if tp2 <= entry:
        return None
    tp1 = entry + (tp2 - entry) * 0.5          # halfway, for the partial

    return {
        "direction": "LONG",
        "entry": round(entry, 8), "stop": round(stop, 8),
        "tp1": round(tp1, 8), "tp2": round(tp2, 8),
        "rr": round((tp2 - entry) / risk, 2),
        "key_level": (f"swept low @ {swing_low:.6g}, FVG entry {entry:.6g}, "
                      f"target old high {tp2:.6g}"),
        "confluence": 1,
        "signal_ts": int(df["timestamp"].iloc[i]),
    }


def detect_mss(df, window=12):
    """Standalone Market Structure Shift: liquidity sweep + close beyond the
    opposite pivot — WITHOUT requiring an FVG (so it's a superset of ICT and
    fires more often). Returns {direction, swept} or None."""
    if len(df) < window + 10:
        return None
    highs, lows = find_swings(df, lookback=2)
    if not highs or not lows:
        return None

    seg = df.iloc[-window:]
    last_close = float(df["close"].iloc[-1])
    swing_high = highs[-1][1]
    swing_low = lows[-1][1]

    if bool((seg["low"] < swing_low).any()) and last_close > swing_high:
        return {"direction": "LONG", "swept": float(swing_low)}
    if bool((seg["high"] > swing_high).any()) and last_close < swing_low:
        return {"direction": "SHORT", "swept": float(swing_high)}
    return None


def detect_ict(df, window=12):
    if len(df) < window + 10:
        return None

    highs, lows = find_swings(df, lookback=2)
    if not highs or not lows:
        return None

    seg = df.iloc[-window:]
    last_close = float(df["close"].iloc[-1])
    swing_high = highs[-1][1]
    swing_low = lows[-1][1]
    fvg = fair_value_gap(df)

    # Bullish: swept liquidity below a swing low, then closed above the swing
    # high (MSS up), with a bullish FVG left behind.
    swept_low = bool((seg["low"] < swing_low).any())
    if swept_low and last_close > swing_high and fvg == "BULLISH":
        return {"direction": "LONG", "swept": float(swing_low)}

    # Bearish mirror.
    swept_high = bool((seg["high"] > swing_high).any())
    if swept_high and last_close < swing_low and fvg == "BEARISH":
        return {"direction": "SHORT", "swept": float(swing_high)}

    return None


if __name__ == "__main__":
    import ccxt
    import pandas as pd

    ex = ccxt.binanceus()
    for coin in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        bars = ex.fetch_ohlcv(coin, timeframe="1h", limit=200)
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        print(coin, "->", detect_ict(df.iloc[:-1]))
