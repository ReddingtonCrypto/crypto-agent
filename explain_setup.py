"""Explain a CRT-Scout setup in full: the exact candles + levels the agent used,
so a human can find them on the chart and validate (or reject) the call.

Usage:  python explain_setup.py BTC/USDT 1w  [ETH/USDT 4h ...]
Read-only. Uses the SAME data the live agent scans (agent._closed_df).
"""
import sys
import datetime as dt

import agent
from strategies.smc.crt import detect_crt_scout
from strategies.smc import key_levels
from strategies.smc.market_structure import find_swings


def _d(ts):
    return dt.datetime.fromtimestamp(ts / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M UTC")


def explain(coin, tf):
    print(f"\n================ {coin} · {tf} ================")
    df = agent._closed_df(coin, tf, {})
    if df is None or len(df) < 56:
        print("  no / insufficient data")
        return
    last = df.iloc[-1]
    print(f"Last closed candle  {_d(last.timestamp)}")
    print(f"     O {last.open:g}  H {last.high:g}  L {last.low:g}  C {last.close:g}")

    s = detect_crt_scout(df, min_confluence=1, min_rr=1.0)
    if not s:
        print("  -> NO real liquidity-sweep CRT here (no alert). Good if you don't see one.")
        return

    i = len(df) - 1
    highs, lows = find_swings(df, lookback=3)
    lvl = s["level"]
    pool = highs if s["direction"] == "SHORT" else lows
    idxs = [idx for (idx, p) in pool if abs(p - lvl) < 1e-9]
    ago = (i - max(idxs)) if idxs else "?"
    side = "high" if s["direction"] == "SHORT" else "low"
    print(f"  SWEEP: wick took the old swing {side} @ {lvl:g} "
          f"(that level formed ~{ago} bars ago) then CLOSED back through it -> {s['direction']}")
    print(f"  KEY LEVEL = that swept swing {side} itself.  Confluence: {s['key_level']}")
    print(f"  PLAN: entry {s['entry']:g}  stop {s['stop']:g} (the sweep wick)  "
          f"TP1 {s['tp1']:g}  TP2 {s['tp2']:g} (opposing liquidity)  R:R {s['rr']}  conf {s['confluence']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    pairs = [(args[k], args[k + 1]) for k in range(0, len(args) - 1, 2)]
    for coin, tf in pairs:
        try:
            explain(coin, tf)
        except Exception as e:
            print(f"  error {coin} {tf}: {type(e).__name__}: {e}")
