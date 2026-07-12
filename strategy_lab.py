"""Fast, honest strategy-research lab — test NON-ICT strategy families.

Vectorised (runs in seconds), fees modelled, LONG/FLAT only (spot), and every
result is split into first-half vs last-half (walk-forward) so we never trust an
aggregate number again. Benchmarked against buy-and-hold.

Run:  python strategy_lab.py [4h|1d]
Uses cached candles in data/bt_cache/ (from the backtester).
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

TF = sys.argv[1] if len(sys.argv) > 1 else "4h"
FEE = 0.001  # 0.1% per side, charged on every position change
BARS_PER_YEAR = {"4h": 6 * 365, "1d": 365, "1h": 24 * 365, "12h": 2 * 365}.get(TF, 365)


def load_coins(tf):
    out = {}
    for path in glob.glob(f"data/bt_cache/*_{tf}.json"):
        coin = os.path.basename(path).replace(f"_{tf}.json", "")
        bars = json.load(open(path))
        if len(bars) < 300:
            continue
        df = pd.DataFrame(bars, columns=["t", "o", "h", "l", "c", "v"])
        out[coin] = df
    return out


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _ffill_state(entry, exit_):
    """Long/flat position from boolean entry/exit signals (stateful, vectorised)."""
    sig = pd.Series(np.nan, index=entry.index)
    sig[entry] = 1.0
    sig[exit_] = 0.0
    return sig.ffill().fillna(0.0)


# ---- Strategy families: each returns a LONG/FLAT position series (0/1) ----
def buy_hold(df):
    return pd.Series(1.0, index=df.index)

def trend_ma(df, n=50):
    return (df["c"] > df["c"].rolling(n).mean()).astype(float)

def momentum(df, n=30):
    return (df["c"] > df["c"].shift(n)).astype(float)

def donchian(df, n=20):
    entry = df["c"] > df["h"].rolling(n).max().shift(1)
    exit_ = df["c"] < df["l"].rolling(n).min().shift(1)
    return _ffill_state(entry, exit_)

def meanrev(df, lo=30, hi=55):
    r = rsi(df["c"])
    return _ffill_state(r < lo, r > hi)

def dual_cross(df, fast=20, slow=100):
    return (df["c"].rolling(fast).mean() > df["c"].rolling(slow).mean()).astype(float)

STRATS = {
    "buy_hold": buy_hold,
    "trend_ma20": lambda d: trend_ma(d, 20),
    "trend_ma50": lambda d: trend_ma(d, 50),
    "trend_ma100": lambda d: trend_ma(d, 100),
    "trend_ma150": lambda d: trend_ma(d, 150),
    "trend_ma200": lambda d: trend_ma(d, 200),
    "dualcross20_100": lambda d: dual_cross(d, 20, 100),
    "momentum30": lambda d: momentum(d, 30),
    "momentum60": lambda d: momentum(d, 60),
    "momentum90": lambda d: momentum(d, 90),
    "donchian20": lambda d: donchian(d, 20),
    "donchian50": lambda d: donchian(d, 50),
    "meanrev_rsi": meanrev,
}


def trades(df, pos):
    """Per-trade returns for a long/flat position (contiguous long runs), net fees."""
    p = pos.fillna(0.0).to_numpy()
    c = df["c"].to_numpy()
    out = []
    in_pos = False
    entry = 0.0
    for i in range(1, len(p)):
        if p[i] == 1 and not in_pos:
            in_pos, entry = True, c[i]
        elif p[i] == 0 and in_pos:
            out.append((c[i] / entry - 1) - 2 * FEE)
            in_pos = False
    if in_pos:  # still open at the end — close at last price
        out.append((c[-1] / entry - 1) - 2 * FEE)
    return out


def net_returns(df, pos):
    """Per-bar net return of a long/flat position, fees on position changes."""
    r = df["c"].pct_change().fillna(0.0)
    gross = pos.shift(1).fillna(0.0) * r
    cost = pos.diff().abs().fillna(0.0) * FEE
    return gross - cost


def stats(port):
    """Total return, annualised Sharpe, max drawdown for a per-bar return series."""
    port = port.dropna()
    if len(port) < 10:
        return 0.0, 0.0, 0.0
    eq = (1 + port).cumprod()
    total = eq.iloc[-1] - 1
    sharpe = (port.mean() / port.std() * np.sqrt(BARS_PER_YEAR)) if port.std() else 0.0
    dd = (eq / eq.cummax() - 1).min()
    return total * 100, sharpe, dd * 100


def main():
    coins = load_coins(TF)
    print(f"Strategy lab — {len(coins)} coins on {TF}, fee {FEE*200:.1f}% round-trip, LONG/FLAT\n")
    print(f"{'Strategy':<15} {'FullRet':>7} {'Sharpe':>6} {'MaxDD':>5} | "
          f"{'1st-h':>7} {'2nd-h':>7} {'W-fwd':>7} | {'Trds':>5} {'Win%':>5} {'Avg/tr':>6}")
    print("-" * 92)
    for name, fn in STRATS.items():
        # Build an equal-weight portfolio: average per-bar net return across coins.
        per_coin = []
        for df in coins.values():
            pos = fn(df).clip(0, 1)
            per_coin.append(net_returns(df, pos).reset_index(drop=True))
        port = pd.concat(per_coin, axis=1).mean(axis=1).dropna()
        half = len(port) // 2
        full_r, sharpe, dd = stats(port)
        first_r, _, _ = stats(port.iloc[:half])
        last_r, _, _ = stats(port.iloc[half:])
        robust = "OK both" if (first_r > 0 and last_r > 0) else "FAILS"
        # Per-trade stats across all coins.
        all_tr = []
        for df in coins.values():
            all_tr += trades(df, fn(df).clip(0, 1))
        n = len(all_tr)
        wr = 100 * sum(1 for t in all_tr if t > 0) / n if n else 0
        avg = 100 * (sum(all_tr) / n) if n else 0
        print(f"{name:<15} {full_r:>7.0f}% {sharpe:>6.2f} {dd:>5.0f}% | "
              f"{first_r:>7.0f}% {last_r:>7.0f}% {robust:>7} | "
              f"{n:>5} {wr:>5.0f}% {avg:>+6.2f}%")


if __name__ == "__main__":
    main()
