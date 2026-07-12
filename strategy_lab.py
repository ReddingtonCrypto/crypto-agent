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

STRATS = {
    "buy_hold": buy_hold,
    "trend_ma50": lambda d: trend_ma(d, 50),
    "trend_ma100": lambda d: trend_ma(d, 100),
    "momentum30": lambda d: momentum(d, 30),
    "donchian20": lambda d: donchian(d, 20),
    "meanrev_rsi": meanrev,
}


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
    print(f"{'Strategy':<13} {'Full ret':>9} {'Sharpe':>7} {'MaxDD':>7} | "
          f"{'1st-half':>9} {'2nd-half':>9}  Verdict")
    print("-" * 78)
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
        robust = "OK both halves" if (first_r > 0 and last_r > 0) else "fails a half"
        print(f"{name:<13} {full_r:>8.0f}% {sharpe:>7.2f} {dd:>6.0f}% | "
              f"{first_r:>8.0f}% {last_r:>8.0f}%  {robust}")


if __name__ == "__main__":
    main()
