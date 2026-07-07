"""Walk-forward validation — the honesty test for "learning from past data".

The danger with any adaptive/optimizing system is curve-fitting: pick the
parameters that worked best on history and they fall apart live. Walk-forward
exposes this directly. For each rolling fold it:

  1. IN-SAMPLE  : picks the best VP bin-count by expectancy on a past window.
  2. OUT-OF-SAMPLE: applies that choice to the NEXT, unseen window and records
                    the result — the only number that means anything.

It then compares two policies over the aggregated out-of-sample trades:
  - ADAPTIVE : use whatever bin-count won in-sample each fold.
  - FIXED-50 : always use the shipped default (VP_BINS = 50).

If ADAPTIVE doesn't clearly beat FIXED out-of-sample, then "auto-tuning from
past data" is just overfitting and we're right to keep parameters fixed.

Run:  python walkforward.py            (uses cached candles)
      python walkforward.py --refresh  (re-download)

Read-only: does not touch the live bot or its database. 1h only, market-bias
tilt off (isolates the parameter question). Reuses the live evaluate/exit code.
"""

import json
import os
import sys

import ccxt
import pandas as pd

import agent
import backtest as bt
from risk_engine import calculate_trade


from data_source import make_exchange
EXCHANGE = make_exchange()  # binance.com global (the live venue), vision host
COINS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
    "LINK/USDT", "AVAX/USDT", "DOT/USDT", "BCH/USDT", "XLM/USDT",
]
TIMEFRAME = "1h"
HISTORY = 1000          # candles per coin (binanceus max per call)
IS_LEN = 500            # in-sample window length (bars)
OOS_LEN = 250           # out-of-sample window length (bars)
STEP = 250              # slide between folds (non-overlapping OOS)
GRID = [30, 50, 70]     # VP bin-counts to choose among
MIN_IS_TRADES = 8       # need this many in-sample trades to trust a pick
FEE = 0.001

CACHE_DIR = "data/wf_cache"
REFRESH = "--refresh" in sys.argv

# Variant C exits (the live config) for the simulator we borrow from backtest.
bt.PARTIAL_MOVE_BE = True


def get_history(coin):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{coin.replace('/', '_')}_{TIMEFRAME}.json")
    if not REFRESH and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    bars = EXCHANGE.fetch_ohlcv(coin, TIMEFRAME, limit=HISTORY)
    with open(path, "w") as f:
        json.dump(bars, f)
    return bars


def run_segment(df, lo, hi, vp_bins):
    """Trade the strategy over bars [lo, hi) with a given VP bin-count. Trades
    may resolve using bars beyond `hi` (realistic). Returns (trades, total_pnl)."""
    agent.ENABLE_VP = True
    agent.VP_BINS = vp_bins
    trades = 0
    pnl_sum = 0.0
    open_until = {}
    for i in range(max(lo, bt.WINDOW), hi):
        window = df.iloc[max(0, i - bt.WINDOW):i + 1]
        res = agent.evaluate(window, "WF", TIMEFRAME, "WF")
        for sig in res["signals"]:
            if not agent.passes_filters(sig):
                continue
            key = (sig["strategy"], sig["direction"])
            if open_until.get(key, -1) >= i:
                continue
            trade = calculate_trade(
                sig["price"], sig["direction"], sig["atr"], sig["strategy"],
                sig.get("stop_level"),
            )
            outcome, pnl, close_bar = bt.simulate_partial(
                df, i, sig["direction"], trade["entry"], trade["stop"]
            )
            if outcome is None:
                continue
            pnl -= FEE * 2 * 100
            open_until[key] = close_bar
            trades += 1
            pnl_sum += pnl
    return trades, pnl_sum


def main():
    frames = {}
    for coin in COINS:
        try:
            frames[coin] = pd.DataFrame(
                get_history(coin),
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            agent.add_indicators(frames[coin])
        except Exception as e:
            print(f"  skip {coin}: {type(e).__name__}: {e}")

    adaptive = {"trades": 0, "pnl": 0.0}
    fixed = {"trades": 0, "pnl": 0.0}
    picks = {b: 0 for b in GRID}
    n_folds = 0

    for ci, (coin, df) in enumerate(frames.items(), 1):
        print(f"[{ci}/{len(frames)}] {coin} ...", flush=True)
        n = len(df)
        start = 0
        while start + IS_LEN + OOS_LEN <= n:
            is_lo, is_hi = start, start + IS_LEN
            oos_lo, oos_hi = is_hi, is_hi + OOS_LEN

            # 1) In-sample: score every bin-count, pick the best (by avg/trade).
            best_bins, best_avg, best_tr = 50, None, 0
            for b in GRID:
                tr, pl = run_segment(df, is_lo, is_hi, b)
                if tr >= MIN_IS_TRADES:
                    avg = pl / tr
                    if best_avg is None or avg > best_avg:
                        best_bins, best_avg, best_tr = b, avg, tr
            picks[best_bins] += 1
            n_folds += 1

            # 2) Out-of-sample: the honest test.
            a_tr, a_pl = run_segment(df, oos_lo, oos_hi, best_bins)
            f_tr, f_pl = run_segment(df, oos_lo, oos_hi, 50)
            adaptive["trades"] += a_tr; adaptive["pnl"] += a_pl
            fixed["trades"] += f_tr; fixed["pnl"] += f_pl

            start += STEP

    print("\n========== WALK-FORWARD (out-of-sample) ==========")
    print(f"Coins: {len(frames)} | {TIMEFRAME} | folds: {n_folds} "
          f"| IS {IS_LEN} / OOS {OOS_LEN} / step {STEP}")
    print(f"In-sample bin-count picks: {picks}\n")
    for name, s in (("ADAPTIVE", adaptive), ("FIXED-50", fixed)):
        tr = s["trades"]
        avg = s["pnl"] / tr if tr else 0.0
        print(f"{name:<10} OOS trades {tr:>4}  total {s['pnl']:>8.1f}%  avg/trade {avg:>6.2f}%")
    print("==================================================")
    print("If ADAPTIVE doesn't clearly beat FIXED-50 out-of-sample, tuning bin")
    print("count from past data is overfitting -- keep it fixed at 50.")


if __name__ == "__main__":
    main()
