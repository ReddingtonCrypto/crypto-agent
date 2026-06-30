"""Backtester — replay history and simulate every trade each strategy would
have taken, so you can compare Trend / Range / ICT without waiting days.

Run:  python backtest.py
It does NOT touch the live bot or the database. Read-only analysis.

Honest limits: past performance != future; a small fee is modelled but real
slippage varies; don't over-tune to these numbers.
"""

import json
import os
import sys

import ccxt
import pandas as pd

import agent
from risk_engine import calculate_trade


EXCHANGE = ccxt.binanceus({"enableRateLimit": True, "timeout": 30000})  # matches live data source
# (If binanceus rate-limits your IP locally, temporarily swap to ccxt.mexc — prices are ~identical.)

CACHE_DIR = "data/bt_cache"
# Pass --refresh on the command line to re-download; otherwise cached candles
# are reused so every run tests on IDENTICAL data (clean A/B comparisons).
REFRESH = "--refresh" in sys.argv


def get_history(coin, timeframe):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{coin.replace('/', '_')}_{timeframe}.json")
    if not REFRESH and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    bars = EXCHANGE.fetch_ohlcv(coin, timeframe, limit=HISTORY)
    with open(path, "w") as f:
        json.dump(bars, f)
    return bars

COINS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
    "AVAX/USDT", "LINK/USDT", "LTC/USDT", "DOT/USDT", "DOGE/USDT",
]
TIMEFRAMES = ["1h", "4h"]   # mirror the live bot's timeframes
HISTORY = 500            # candles to pull per coin/timeframe
WINDOW = 160             # trailing candles handed to the strategy each bar
FEE = 0.001              # 0.1% per side modelled on the result
MAX_HOLD = 200           # give a trade this many bars to resolve, else drop


# Break-even stop: BACKTESTED at 0.5 and 0.8 triggers — both HURT (cuts winners
# short). Left here, OFF, for future experiments. Don't enable without re-testing.
USE_BE = False
BE_TRIGGER_FRAC = 0.8    # fraction of the way to TP1 before moving stop to entry


def simulate(df, i, direction, entry, stop, tp1):
    """Walk forward from bar i+1; return (outcome, exit_price, close_bar) using
    candle highs/lows, or (None, None, None) if it never resolves.

    If USE_BE: once price runs BE_TRIGGER_FRAC of the way to TP1, the stop is
    moved to entry (break-even), so a winner that reverses exits at ~0 instead
    of a full loss."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    end = min(len(df), i + 1 + MAX_HOLD)
    cur_stop = stop
    armed = False
    if direction == "LONG":
        be_trigger = entry + BE_TRIGGER_FRAC * (tp1 - entry)
    else:
        be_trigger = entry - BE_TRIGGER_FRAC * (entry - tp1)

    for k in range(i + 1, end):
        hi, lo = highs[k], lows[k]
        if direction == "LONG":
            if USE_BE and not armed and hi >= be_trigger:
                armed, cur_stop = True, entry
            if lo <= cur_stop:
                return ("WIN" if cur_stop > entry else "LOSS"), cur_stop, k
            if hi >= tp1:
                return "WIN", tp1, k
        else:
            if USE_BE and not armed and lo <= be_trigger:
                armed, cur_stop = True, entry
            if hi >= cur_stop:
                return ("WIN" if cur_stop < entry else "LOSS"), cur_stop, k
            if lo <= tp1:
                return "WIN", tp1, k
    return None, None, None


def backtest_one(coin, timeframe, stats):
    bars = get_history(coin, timeframe)
    df = pd.DataFrame(
        bars, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    agent.add_indicators(df)
    n = len(df)

    # When a (strategy,direction) trade is open, don't open another until it
    # closes — mirrors the live one-at-a-time rule.
    open_until = {}

    for i in range(60, n - 1):
        window = df.iloc[max(0, i - WINDOW):i + 1]
        res = agent.evaluate(window, coin, timeframe, "BT")
        for sig in res["signals"]:
            if not agent.passes_filters(sig):
                continue
            key = (sig["strategy"], sig["direction"])
            if open_until.get(key, -1) >= i:
                continue  # a trade of this kind is still open

            trade = calculate_trade(
                sig["price"], sig["direction"], sig["atr"], sig["strategy"],
                sig.get("stop_level"),
            )
            outcome, exit_price, close_bar = simulate(
                df, i, sig["direction"], trade["entry"], trade["stop"], trade["tp1"]
            )
            if outcome is None:
                continue

            pnl = (exit_price - trade["entry"]) / trade["entry"] * 100.0
            if sig["direction"] == "SHORT":
                pnl = -pnl
            pnl -= FEE * 2 * 100  # entry + exit fees

            open_until[key] = close_bar

            s = stats.setdefault(sig["strategy"], {"wins": 0, "losses": 0, "pnl": 0.0})
            if outcome == "WIN":
                s["wins"] += 1
            else:
                s["losses"] += 1
            s["pnl"] += pnl


def main():
    stats = {}
    for coin in COINS:
        for tf in TIMEFRAMES:
            try:
                backtest_one(coin, tf, stats)
                print(f"  done {coin} {tf}")
            except Exception as e:
                print(f"  skip {coin} {tf}: {type(e).__name__}: {e}")

    print("\n========== BACKTEST RESULTS ==========")
    print(f"Coins: {len(COINS)} | Timeframes: {TIMEFRAMES} | ~{HISTORY} candles each")
    print(f"Fee modelled: {FEE*200:.1f}% round-trip\n")
    print(f"{'Strategy':<10} {'Trades':>7} {'WinRate':>8} {'TotalPnL':>9} {'Avg/Trade':>10}")
    for strat in sorted(stats, key=lambda k: stats[k]["pnl"], reverse=True):
        s = stats[strat]
        trades = s["wins"] + s["losses"]
        wr = s["wins"] / trades * 100 if trades else 0
        avg = s["pnl"] / trades if trades else 0
        print(f"{strat:<10} {trades:>7} {wr:>7.1f}% {s['pnl']:>8.1f}% {avg:>9.2f}%")
    print("======================================")


if __name__ == "__main__":
    main()
