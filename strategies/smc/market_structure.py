"""
SMC Stage 2 — Market Structure (BOS / CHoCH).

Objective, measurable definitions (see SPEC.md section 6):

- Swing high : a candle whose HIGH is the highest within `lookback` bars on
               BOTH sides. Swing low is the mirror.
- Up structure : making Higher Highs and Higher Lows.
- Down structure: making Lower Highs and Lower Lows.
- BOS  (Break of Structure)    : price closes BEYOND the most recent swing in
                                 the SAME direction as the trend -> continuation.
- CHoCH (Change of Character)   : price closes beyond a swing AGAINST the current
                                 trend -> possible reversal.

Pure functions, no network, no side effects. Feed it a DataFrame with
columns: open, high, low, close.
"""


def find_swings(df, lookback=2):
    """Return two lists of (index, price) for swing highs and swing lows.

    A swing needs `lookback` candles on each side, so the most recent
    `lookback` candles can never be swings yet (not enough right-side data).
    """
    highs = []
    lows = []

    # Use numpy arrays (not pandas .iloc) so this is fast enough to call
    # thousands of times during a backtest.
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    n = len(h)

    for i in range(lookback, n - lookback):
        if h[i] == h[i - lookback:i + lookback + 1].max():
            highs.append((i, float(h[i])))
        if l[i] == l[i - lookback:i + lookback + 1].min():
            lows.append((i, float(l[i])))

    return highs, lows


def detect_structure(df, lookback=2):
    """Analyse the latest closed candle against recent swings.

    Returns a dict:
      {
        "trend":   "UP" | "DOWN" | "UNKNOWN",
        "event":   "BOS" | "CHoCH" | None,
        "direction": "BULLISH" | "BEARISH" | None,
        "level":   the swing price that was broken (or None),
        "close":   latest close,
      }
    """
    highs, lows = find_swings(df, lookback)

    result = {
        "trend": "UNKNOWN",
        "event": None,
        "direction": None,
        "level": None,
        "close": float(df["close"].iloc[-1]),
    }

    # Need at least two of each swing to read a trend.
    if len(highs) < 2 or len(lows) < 2:
        return result

    last_high = highs[-1][1]
    prev_high = highs[-2][1]
    last_low = lows[-1][1]
    prev_low = lows[-2][1]

    # Current structural trend from the last two swings of each.
    if last_high > prev_high and last_low > prev_low:
        result["trend"] = "UP"
    elif last_high < prev_high and last_low < prev_low:
        result["trend"] = "DOWN"
    else:
        result["trend"] = "UNKNOWN"

    close = result["close"]

    # Did the latest close break the most recent swing high / low?
    broke_high = close > last_high
    broke_low = close < last_low

    if broke_high:
        # Breaking up: continuation if already UP (BOS), else reversal (CHoCH).
        result["direction"] = "BULLISH"
        result["level"] = last_high
        result["event"] = "BOS" if result["trend"] == "UP" else "CHoCH"

    elif broke_low:
        # Breaking down: continuation if already DOWN (BOS), else reversal (CHoCH).
        result["direction"] = "BEARISH"
        result["level"] = last_low
        result["event"] = "BOS" if result["trend"] == "DOWN" else "CHoCH"

    return result


# --- quick self-test on real OKX data (run this file directly) ---
if __name__ == "__main__":
    import ccxt
    import pandas as pd

    exchange = ccxt.okx()

    for coin in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        bars = exchange.fetch_ohlcv(coin, timeframe="1h", limit=200)
        df = pd.DataFrame(
            bars,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )

        s = detect_structure(df, lookback=2)

        print(f"\n{coin}")
        print(f"  trend : {s['trend']}")
        print(f"  close : {s['close']}")
        if s["event"]:
            print(f"  EVENT : {s['event']} ({s['direction']}) broke level {s['level']}")
        else:
            print("  EVENT : none (no fresh break of structure)")
