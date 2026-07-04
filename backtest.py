"""Backtester — replay history and simulate every trade each strategy would
have taken, so you can compare Trend / Range / ICT without waiting days.

Run:  python backtest.py
It does NOT touch the live bot or the database. Read-only analysis.

Honest limits: past performance != future; a small fee is modelled but real
slippage varies; don't over-tune to these numbers.
"""

import bisect
import json
import os
import sys

import ccxt
import pandas as pd

import agent
import universe
from risk_engine import calculate_trade

# The Volume Profile POC filter now lives in agent.passes_filters (so the
# backtest mirrors live automatically). A/B it from the CLI:
#   --no-vp        : disable the VP filter for this run.
#   --vp-bins=N    : override the profile resolution (default 50).
if "--no-vp" in sys.argv:
    agent.ENABLE_VP = False
for _a in sys.argv:
    if _a.startswith("--vp-bins="):
        agent.VP_BINS = int(_a.split("=", 1)[1])


# MEXC for the backtest data source: reachable everywhere, prices ~identical to
# binanceus (the live venue). Switch to ccxt.binanceus if you prefer exact match.
EXCHANGE = ccxt.binanceus({"enableRateLimit": True, "timeout": 30000})

USE_MARKET_FILTER = True   # apply the live BTC market-bias tilt at each bar
COINS_LIMIT = 20           # mirror live: top 20 by market cap (alts drag the edge)

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

# Majors vs majors+alts test: top ~19 majors, then a batch of smaller alts.
MAJORS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT", "BNB/USDT",
    "TRX/USDT", "LINK/USDT", "AVAX/USDT", "DOT/USDT", "LTC/USDT", "BCH/USDT",
    "DOGE/USDT", "UNI/USDT", "ATOM/USDT", "XLM/USDT", "NEAR/USDT", "APT/USDT",
    "AAVE/USDT",
]
ALTS = [
    "ARB/USDT", "OP/USDT", "SUI/USDT", "INJ/USDT", "SEI/USDT", "TIA/USDT",
    "RUNE/USDT", "ALGO/USDT", "FIL/USDT", "HBAR/USDT", "IMX/USDT", "GRT/USDT",
    "SAND/USDT", "MANA/USDT", "AXS/USDT", "ETC/USDT", "CRV/USDT", "RENDER/USDT",
    "ENA/USDT",
]
TIMEFRAMES = ["1h", "4h"]   # mirror the live bot's timeframes
HISTORY = 500            # candles to pull per coin/timeframe
WINDOW = 160             # trailing candles handed to the strategy each bar
FEE = 0.001              # 0.1% per side modelled on the result
MAX_HOLD = 200           # give a trade this many bars to resolve, else drop


class BTCContext:
    """Pre-computes BTC's daily + 4h trend over history so the backtest can ask
    'what was the market bias at timestamp T?' — exactly like the live bot, with
    no look-ahead (only uses BTC candles already CLOSED by T)."""

    PERIODS = {"1d": 86_400_000, "4h": 14_400_000}

    def __init__(self):
        self.daily_t, self.daily_d = self._load("1d")
        self.h4_t, self.h4_d = self._load("4h")

    def _load(self, tf):
        df = pd.DataFrame(
            get_history("BTC/USDT", tf),
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        agent.add_indicators(df)
        period = self.PERIODS[tf]
        times, dirs = [], []
        for idx in range(len(df)):
            ema50 = df["EMA50"].iat[idx]
            if pd.isna(ema50):
                continue
            times.append(int(df["timestamp"].iat[idx]) + period)  # candle CLOSE time
            dirs.append("LONG" if df["EMA20"].iat[idx] > ema50 else "SHORT")
        return times, dirs

    @staticmethod
    def _dir(times, dirs, ts):
        k = bisect.bisect_right(times, ts) - 1
        return dirs[k] if k >= 0 else None

    def bias_at(self, ts):
        d = self._dir(self.daily_t, self.daily_d, ts)
        h = self._dir(self.h4_t, self.h4_d, ts)
        if d is None or h is None:
            return "BOTH"
        return d if d == h else "BOTH"


# Break-even stop: BACKTESTED at 0.5 and 0.8 triggers — both HURT (cuts winners
# short). Left here, OFF, for future experiments. Don't enable without re-testing.
USE_BE = False
BE_TRIGGER_FRAC = 0.8    # fraction of the way to TP1 before moving stop to entry

# Trailing stop: after price runs TRAIL_ARM_R risk-multiples in favour, trail the
# stop TRAIL_DIST_R behind the best price (no fixed TP — let winners run).
# Trailing stop: BACKTESTED arm1R/trail1.5R (-0.28%) and arm2R/trail3R (-1.68%)
# — both HURT badly. ICT's edge is BANKING the fixed 2R/3R structure target;
# letting winners "run" gives it back. Leave OFF. Don't change ICT's exits.
USE_TRAIL = False
TRAIL_ARM_R = 1.0
TRAIL_DIST_R = 1.5

# Partial-exit mode: bank PARTIAL_FRAC of the position at TP1_R, let the runner
# go to TP2_R. Optionally move the runner's stop to break-even after TP1. This
# targets PROFIT-PER-TRADE (bigger avg winner) without adding any entries.
# Enable with --partial on the command line. Overrides the fixed-TP path below.
USE_PARTIAL = "--partial" in sys.argv
PARTIAL_FRAC = 0.5       # fraction banked at TP1
TP1_R = 2.0              # first target in risk-multiples (banked)
TP2_R = 4.0             # runner target in risk-multiples
PARTIAL_MOVE_BE = "--partial-be" in sys.argv   # runner stop -> entry after TP1


def simulate(df, i, direction, entry, stop, tp1):
    """Walk forward from bar i+1; return (outcome, exit_price, close_bar) using
    candle highs/lows, or (None, None, None) if it never resolves."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    end = min(len(df), i + 1 + MAX_HOLD)
    cur_stop = stop
    R = abs(entry - stop) or 1e-9

    # ----- Trailing-stop mode (no fixed TP) -----
    if USE_TRAIL:
        if direction == "LONG":
            arm = entry + TRAIL_ARM_R * R
            peak = entry
            armed = False
            for k in range(i + 1, end):
                hi, lo = highs[k], lows[k]
                if hi > peak:
                    peak = hi
                if not armed and hi >= arm:
                    armed = True
                if armed:
                    cur_stop = max(cur_stop, peak - TRAIL_DIST_R * R)
                if lo <= cur_stop:
                    return ("WIN" if cur_stop > entry else "LOSS"), cur_stop, k
        else:
            arm = entry - TRAIL_ARM_R * R
            trough = entry
            armed = False
            for k in range(i + 1, end):
                hi, lo = highs[k], lows[k]
                if lo < trough:
                    trough = lo
                if not armed and lo <= arm:
                    armed = True
                if armed:
                    cur_stop = min(cur_stop, trough + TRAIL_DIST_R * R)
                if hi >= cur_stop:
                    return ("WIN" if cur_stop < entry else "LOSS"), cur_stop, k
        return None, None, None

    # ----- Fixed TP1 / stop mode (with optional break-even) -----
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


def simulate_partial(df, i, direction, entry, stop):
    """Partial-exit walk: bank PARTIAL_FRAC at TP1_R, run the rest to TP2_R
    (optionally moving the runner stop to break-even after TP1).

    Returns (label, pnl_pct_gross, close_bar) where pnl_pct_gross already blends
    both legs and is signed for the trade direction, or (None, None, None) if
    the trade never even reached TP1 or its stop (dropped, like the fixed path).
    """
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    end = min(len(df), i + 1 + MAX_HOLD)
    R = abs(entry - stop) or 1e-9

    if direction == "LONG":
        tp1, tp2 = entry + TP1_R * R, entry + TP2_R * R
    else:
        tp1, tp2 = entry - TP1_R * R, entry - TP2_R * R

    def leg(exit_price):
        """Signed % return of one price level for this trade's direction."""
        r = (exit_price - entry) / entry * 100.0
        return -r if direction == "SHORT" else r

    banked = False
    realized = 0.0
    run_stop = stop

    for k in range(i + 1, end):
        hi, lo = highs[k], lows[k]
        if direction == "LONG":
            if not banked:
                if lo <= stop:                      # stop before TP1 -> full loss
                    return "LOSS", leg(stop), k
                if hi >= tp1:                       # bank the partial
                    banked = True
                    realized += PARTIAL_FRAC * leg(tp1)
                    if PARTIAL_MOVE_BE:
                        run_stop = entry
            else:
                if lo <= run_stop:
                    total = realized + (1 - PARTIAL_FRAC) * leg(run_stop)
                    return ("WIN" if total > 0 else "LOSS"), total, k
                if hi >= tp2:
                    total = realized + (1 - PARTIAL_FRAC) * leg(tp2)
                    return "WIN", total, k
        else:
            if not banked:
                if hi >= stop:
                    return "LOSS", leg(stop), k
                if lo <= tp1:
                    banked = True
                    realized += PARTIAL_FRAC * leg(tp1)
                    if PARTIAL_MOVE_BE:
                        run_stop = entry
            else:
                if hi >= run_stop:
                    total = realized + (1 - PARTIAL_FRAC) * leg(run_stop)
                    return ("WIN" if total > 0 else "LOSS"), total, k
                if lo <= tp2:
                    total = realized + (1 - PARTIAL_FRAC) * leg(tp2)
                    return "WIN", total, k

    # Ran out of bars. If the partial was banked, close the runner at the last
    # close and count it; if TP1 was never reached, drop it (like the fixed path).
    if banked:
        last_close = float(df["close"].iat[end - 1])
        total = realized + (1 - PARTIAL_FRAC) * leg(last_close)
        return ("WIN" if total > 0 else "LOSS"), total, end - 1
    return None, None, None


def backtest_one(coin, timeframe, stats, btc_ctx):
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

        # Market-bias tilt at this bar's time (same as live), no look-ahead.
        bias = btc_ctx.bias_at(int(df["timestamp"].iat[i])) if btc_ctx else "BOTH"

        for sig in res["signals"]:
            if bias != "BOTH":
                if sig["direction"] == bias:
                    sig["confidence"] = min(100, sig["confidence"] + 5)
                else:
                    sig["confidence"] = max(0, sig["confidence"] - 15)
            if not agent.passes_filters(sig):
                continue
            key = (sig["strategy"], sig["direction"])
            if open_until.get(key, -1) >= i:
                continue  # a trade of this kind is still open

            trade = calculate_trade(
                sig["price"], sig["direction"], sig["atr"], sig["strategy"],
                sig.get("stop_level"),
            )
            if USE_PARTIAL:
                outcome, pnl, close_bar = simulate_partial(
                    df, i, sig["direction"], trade["entry"], trade["stop"]
                )
                if outcome is None:
                    continue
                pnl -= FEE * 2 * 100  # entry + exit fees (approx; partial has an extra exit)
            else:
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
    if "--majors" in sys.argv:
        coins = MAJORS
    elif "--alts" in sys.argv:
        coins = MAJORS + ALTS
    else:
        # Mirror live: top coins by market cap available on the exchange.
        # `--top=N` overrides the default COINS_LIMIT for A/B on universe size.
        top_n = COINS_LIMIT
        for a in sys.argv:
            if a.startswith("--top="):
                top_n = int(a.split("=", 1)[1])
        try:
            coins = universe.get_universe(EXCHANGE, 100)[:top_n]
        except Exception as e:
            print(f"universe unavailable ({type(e).__name__}); using fallback list")
            coins = COINS
    print(f"Universe: {len(coins)} coins | market filter: {USE_MARKET_FILTER}")

    btc_ctx = BTCContext() if USE_MARKET_FILTER else None

    stats = {}
    for coin in coins:
        for tf in TIMEFRAMES:
            try:
                backtest_one(coin, tf, stats, btc_ctx)
            except Exception as e:
                print(f"  skip {coin} {tf}: {type(e).__name__}: {e}")

    print("\n========== BACKTEST RESULTS ==========")
    print(f"Coins: {len(coins)} | Timeframes: {TIMEFRAMES} | market filter: {USE_MARKET_FILTER}")
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
