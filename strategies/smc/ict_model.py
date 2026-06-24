"""ICT model strategy — the classic 2022 sequence, as a strict entry trigger:

    1. Liquidity sweep  : price grabs a recent swing high/low (stops).
    2. Market Structure : it then CLOSES beyond the opposite pivot (MSS) — the
       Shift (MSS)         turn is confirmed.
    3. Fair Value Gap   : the impulsive move leaves an FVG in the new direction.

Only when all three line up do we emit a signal. This is a mechanical
approximation of a discretionary concept, so it fires rarely (by design).

Returns {"direction": "LONG"|"SHORT", "swept": level} or None.
"""

from strategies.smc.market_structure import find_swings
from strategies.smc.smc_features import fair_value_gap


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
