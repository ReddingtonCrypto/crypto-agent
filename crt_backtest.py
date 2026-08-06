"""CRT (Candle Range Theory) backtester — a faithful, mechanical test of the
strategy taught in the CRT video series (C1 range -> C2 sweep + close back in ->
C3 delivers the C1-body target), with the taught layers testable as toggles.

This is a SEPARATE, read-only lab tool. It does NOT touch the live bot or DB.
It reuses the same candle cache (data/bt_cache), fee model, and universe as
backtest.py, so CRT numbers are directly comparable to ICT's.

The strategy, reduced to code
----------------------------
For each pair of closed candles C1 (prior) and C2 (current), evaluated at C2:
  Bearish CRT (SHORT):  C2.high > C1.high  (swept C1's high)
                        AND  C1.low <= C2.close <= C1.high  (body closed back in)
      entry = C2.close   stop = C2.high   target = min(C1.open, C1.close)  (C1 body low)
  Bullish CRT (LONG):   C2.low < C1.low
                        AND  C1.low <= C2.close <= C1.high
      entry = C2.close   stop = C2.low    target = max(C1.open, C1.close)  (C1 body high)

Win = target reached before the C2 extreme (the "protected" line) is touched.
Loss = C2 extreme touched first.  Unresolved within MAX_HOLD bars = time-stop
at the last close (EXPIRED, kept in the average — mirrors the live tracker).

Layers (the taught rules), each an opt-in flag so we can measure its contribution:
  --trend      only WITH the trend (EMA20>EMA50 -> longs only; < -> shorts only)
  --keylevel   CRT must sit at a key level: C2 swept a recent KL_LOOKBACK-bar
               extreme (old-high/low liquidity), not just C1
  --confirm    C3 entry: wait for the next candle to body-close in the trade
               direction beyond C2's body (single-candle distribution confirm),
               enter at C3 close instead of C2 close

Validation flags:
  --split=first / --split=last   walk-forward: run only the first/last half of
                                 each coin's history (early vs unseen regime)
  --long-only                    skip shorts (matches spot-only trading)
  --tf=4h,12h,1d   --history=N   --top=N   --jobs=N   --refresh

Timeframes: Daily (1d) + Weekly (1w) only — the exact charts the CRT series
tells beginners to use. (H4/H1 are the paired *entry* TFs for a later multi-TF
rung; 12h/30m are not part of the strategy and are excluded.)

Run:  python crt_backtest.py --trend --keylevel --history=4000 --top=40 --jobs=4
"""

import json
import os
import sys

import pandas as pd

import agent
import universe
from data_source import make_exchange

EXCHANGE = make_exchange()
CACHE_DIR = "data/bt_cache"
REFRESH = "--refresh" in sys.argv

FEE = 0.001          # 0.1% per side -> 0.2% round-trip, same as backtest.py
MAX_HOLD = 200       # bars to give a trade before the time-stop closes it
KL_LOOKBACK = 20     # bars back a sweep must exceed to count as an "old high/low"

# ---- flags ----
USE_TREND = "--trend" in sys.argv
USE_KEYLEVEL = "--keylevel" in sys.argv
USE_CONFIRM = "--confirm" in sys.argv
LONG_ONLY = "--long-only" in sys.argv
SPLIT = None
# CRT-formation timeframes exactly as taught: beginners use Daily and Weekly
# (the series' recommended charts). H4/H1 are the *entry* timeframes in the
# taught pairs (Weekly->H4, Daily->H1); a later multi-TF rung can add those.
# 12h/30m are NOT part of the strategy and are deliberately excluded.
TIMEFRAMES = ["1d", "1w"]
HISTORY = 4000
TOP_N = 40
JOBS = 1
for _a in sys.argv:
    if _a.startswith("--split="):
        SPLIT = _a.split("=", 1)[1]
    if _a.startswith("--tf="):
        TIMEFRAMES = _a.split("=", 1)[1].split(",")
    if _a.startswith("--history="):
        HISTORY = int(_a.split("=", 1)[1])
    if _a.startswith("--top="):
        TOP_N = int(_a.split("=", 1)[1])
    if _a.startswith("--jobs="):
        JOBS = int(_a.split("=", 1)[1])


def get_history(coin, timeframe):
    """Same cache format/location as backtest.py, so candles are shared."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{coin.replace('/', '_')}_{timeframe}.json")
    if not REFRESH and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    if HISTORY <= 1000:
        bars = EXCHANGE.fetch_ohlcv(coin, timeframe, limit=HISTORY)
    else:
        tf_ms = EXCHANGE.parse_timeframe(timeframe) * 1000
        # Clamp to >=0: for weekly, HISTORY*tf_ms can predate the Unix epoch,
        # producing a negative startTime that Binance rejects. Fetching from 0
        # just returns from the coin's earliest listing, which is what we want.
        since = max(0, EXCHANGE.milliseconds() - HISTORY * tf_ms)
        bars = []
        while len(bars) < HISTORY:
            batch = EXCHANGE.fetch_ohlcv(coin, timeframe, since=since, limit=1000)
            if not batch:
                break
            bars += batch
            since = batch[-1][0] + tf_ms
            if len(batch) < 1000:
                break
        seen, uniq = set(), []
        for b in bars:
            if b[0] not in seen:
                seen.add(b[0]); uniq.append(b)
        bars = uniq
    with open(path, "w") as f:
        json.dump(bars, f)
    return bars


def _trend_dir(df, i):
    """Same EMA backbone the live bot uses as its regime signal."""
    e20, e50 = df["EMA20"].iat[i], df["EMA50"].iat[i]
    if pd.isna(e20) or pd.isna(e50):
        return None
    return "LONG" if e20 > e50 else "SHORT"


def _simulate(highs, lows, closes, i, direction, entry, stop, target):
    """Walk forward from bar i+1. Returns (result, pnl_pct, risk_pct).
    result in {WIN, LOSS, EXPIRED}. pnl is after round-trip fees."""
    risk_pct = abs(entry - stop) / entry * 100.0
    end = min(len(highs), i + 1 + MAX_HOLD)
    for k in range(i + 1, end):
        hi, lo = highs[k], lows[k]
        if direction == "LONG":
            if lo <= stop:                       # protected line touched -> loss
                gross = (stop - entry) / entry * 100.0
                return "LOSS", gross - FEE * 200, risk_pct
            if hi >= target:                     # C1 body reached -> win
                gross = (target - entry) / entry * 100.0
                return "WIN", gross - FEE * 200, risk_pct
        else:
            if hi >= stop:
                gross = (entry - stop) / entry * 100.0
                return "LOSS", gross - FEE * 200, risk_pct
            if lo <= target:
                gross = (entry - target) / entry * 100.0
                return "WIN", gross - FEE * 200, risk_pct
    # Time-stop: close at the last available close, keep it in the average.
    last = closes[end - 1]
    gross = ((last - entry) if direction == "LONG" else (entry - last)) / entry * 100.0
    return "EXPIRED", gross - FEE * 200, risk_pct


def backtest_coin(coin):
    """Return a stats dict for one coin across all timeframes."""
    st = {"wins": 0, "losses": 0, "expired": 0, "pnl": 0.0,
          "win_pnl": 0.0, "loss_pnl": 0.0, "r_sum": 0.0, "n": 0,
          "bh_pnl": 0.0, "bh_n": 0}
    for tf in TIMEFRAMES:
        bars = get_history(coin, tf)
        if len(bars) > HISTORY:
            bars = bars[-HISTORY:]
        if SPLIT == "first":
            bars = bars[: len(bars) // 2]
        elif SPLIT == "last":
            bars = bars[len(bars) // 2:]
        if len(bars) < 120:
            continue

        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        agent.add_indicators(df)
        o = df["open"].to_numpy(); h = df["high"].to_numpy()
        lo = df["low"].to_numpy(); c = df["close"].to_numpy()

        # Buy-and-hold benchmark over this exact window (the crypto reality check).
        if len(c) > 1 and c[0] > 0:
            st["bh_pnl"] += (c[-1] - c[0]) / c[0] * 100.0
            st["bh_n"] += 1

        start = max(KL_LOOKBACK + 1, 55)   # need history for MAs + key-level lookback
        for i in range(start, len(df) - 1):
            c1_hi, c1_lo, c1_o, c1_c = h[i - 1], lo[i - 1], o[i - 1], c[i - 1]
            c2_hi, c2_lo, c2_close = h[i], lo[i], c[i]

            swept_high = c2_hi > c1_hi
            swept_low = c2_lo < c1_lo
            closed_in = c1_lo <= c2_close <= c1_hi
            if not closed_in:
                continue
            if swept_high == swept_low:        # neither, or ambiguous outside-bar
                continue

            if swept_high:
                direction = "SHORT"
                entry, stop = c2_close, c2_hi
                target = min(c1_o, c1_c)        # C1 body low
                if target >= entry:             # no room left to the target
                    continue
            else:
                direction = "LONG"
                entry, stop = c2_close, c2_lo
                target = max(c1_o, c1_c)        # C1 body high
                if target <= entry:
                    continue

            if LONG_ONLY and direction == "SHORT":
                continue

            # --- Layer: with-trend only ---
            if USE_TREND:
                td = _trend_dir(df, i)
                if td is None or td != direction:
                    continue

            # --- Layer: key level (swept a recent KL_LOOKBACK-bar extreme) ---
            if USE_KEYLEVEL:
                if direction == "SHORT":
                    if c2_hi < h[i - KL_LOOKBACK:i].max():
                        continue
                else:
                    if c2_lo > lo[i - KL_LOOKBACK:i].min():
                        continue

            enter_i = i
            # --- Layer: C3 confirmation (single-candle distribution close) ---
            if USE_CONFIRM:
                j = i + 1
                if j >= len(df):
                    continue
                c2_body_lo, c2_body_hi = min(o[i], c[i]), max(o[i], c[i])
                if direction == "SHORT" and not (c[j] < c2_body_lo):
                    continue
                if direction == "LONG" and not (c[j] > c2_body_hi):
                    continue
                entry = c[j]                    # enter on C3 close
                # target/stop unchanged; re-check room after the shifted entry
                if direction == "SHORT" and target >= entry:
                    continue
                if direction == "LONG" and target <= entry:
                    continue
                enter_i = j

            result, pnl, risk = _simulate(h, lo, c, enter_i, direction, entry, stop, target)
            st["n"] += 1
            st["pnl"] += pnl
            st["r_sum"] += pnl / risk if risk else 0.0
            if result == "WIN":
                st["wins"] += 1; st["win_pnl"] += pnl
            elif result == "LOSS":
                st["losses"] += 1; st["loss_pnl"] += pnl
            else:
                st["expired"] += 1
                # count expired toward win/loss by sign for the win-rate view
                if pnl > 0:
                    st["win_pnl"] += pnl
                else:
                    st["loss_pnl"] += pnl
    return st


def main():
    try:
        coins = universe.get_universe(EXCHANGE, 100)[:TOP_N]
    except Exception as e:
        print(f"universe unavailable ({type(e).__name__}); using majors fallback")
        coins = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
                 "AVAX/USDT", "LINK/USDT", "LTC/USDT", "DOT/USDT", "DOGE/USDT"]

    layers = []
    if USE_TREND: layers.append("trend")
    if USE_KEYLEVEL: layers.append("keylevel")
    if USE_CONFIRM: layers.append("C3-confirm")
    if LONG_ONLY: layers.append("long-only")
    print(f"CRT backtest | coins={len(coins)} | tfs={TIMEFRAMES} | history={HISTORY}"
          f" | split={SPLIT or 'full'} | layers={layers or ['BASE']}")
    print(f"Fee modelled: {FEE*200:.1f}% round-trip\n")

    total = {"wins": 0, "losses": 0, "expired": 0, "pnl": 0.0,
             "win_pnl": 0.0, "loss_pnl": 0.0, "r_sum": 0.0, "n": 0,
             "bh_pnl": 0.0, "bh_n": 0}

    def merge(dst, src):
        for k in dst:
            dst[k] += src[k]

    if JOBS > 1 and len(coins) > 1:
        import multiprocessing as mp
        with mp.Pool(min(JOBS, len(coins))) as pool:
            for st in pool.imap_unordered(backtest_coin, coins):
                merge(total, st)
    else:
        for coin in coins:
            try:
                merge(total, backtest_coin(coin))
            except Exception as e:
                print(f"  skip {coin}: {type(e).__name__}: {e}")

    n = total["n"]
    resolved = total["wins"] + total["losses"]
    print("========== CRT RESULTS ==========")
    if n == 0:
        print("No CRT setups found.")
        print("=================================")
        return
    win_rate = total["wins"] / resolved * 100 if resolved else 0
    avg = total["pnl"] / n
    avg_win = total["win_pnl"] / total["wins"] if total["wins"] else 0
    n_loss = total["losses"] + max(0, total["expired"])  # rough denom for avg loss
    avg_loss = total["loss_pnl"] / total["losses"] if total["losses"] else 0
    avg_r = total["r_sum"] / n
    bh = total["bh_pnl"] / total["bh_n"] if total["bh_n"] else 0
    print(f"Trades (incl. expired): {n}")
    print(f"  wins={total['wins']}  losses={total['losses']}  expired={total['expired']}")
    print(f"Win rate (resolved)   : {win_rate:.1f}%")
    print(f"Avg P&L / trade        : {avg:+.2f}%   (after fees)")
    print(f"Total P&L              : {total['pnl']:+.1f}%")
    print(f"Avg winner / loser     : +{avg_win:.2f}% / {avg_loss:.2f}%")
    print(f"Avg R multiple / trade : {avg_r:+.2f}R")
    print(f"Buy-and-hold benchmark : {bh:+.1f}% avg per coin over the window")
    print("=================================")


if __name__ == "__main__":
    main()
