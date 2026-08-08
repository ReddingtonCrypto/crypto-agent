"""OTE / "Textbook Setup" (ICT-2022) backtester — a faithful, mechanical,
walk-forward test of the model in strategies/smc/ote.py, kept SEPARATE from CRT.

Read-only lab tool. Does NOT touch the live bot or DB. Reuses the same candle
cache (data/bt_cache), fee model, and universe as backtest.py / crt_backtest.py,
so OTE numbers are directly comparable to ICT's and CRT's.

The model (see strategies/smc/ote.py for the full definition):
  sweep of prior liquidity -> displacement (FVG) + MSS (close beyond the STH/STL)
  -> retrace into the 0.705-0.786 OTE band (limit) -> stop beyond the origin,
  target the NEXT resting liquidity.

Two-stage simulation, because OTE is a LIMIT entry:
  Stage 1  after the MSS bar, wait up to ENTRY_WINDOW bars for price to retrace
           into the OTE band and fill the limit. If price runs to target first,
           the trade is MISSED (an unfilled limit is not a trade); if it never
           retraces, it EXPIRES UNFILLED. Neither counts in the P&L stats.
  Stage 2  from the fill bar, run target vs stop (intrabar high/low), conservative
           on same-bar ties (counts as a loss), MAX_HOLD time-stop otherwise.
Fill rate is reported separately so the "does it even fill" question is explicit.

Flags:
  --no-fvg            drop the displacement-FVG requirement (looser, more setups)
  --long-only / --short-only
  --split=first|last  walk-forward: first vs last half of each coin's history
  --ote=0.75          the fib level the limit rests at inside the OTE band
  --min-rr=1.5        discard setups below this target/stop reward:risk
  --entry-window=20   bars to wait for the retrace fill
  --tf=4h,1d   --history=N   --top=N   --jobs=N   --refresh

Run:  python ote_backtest.py --tf=4h,1d --history=8000 --top=40 --jobs=4
      python ote_backtest.py --tf=4h,1d --split=first ; ... --split=last
"""

import bisect
import json
import os
import sys

import pandas as pd

import universe
from data_source import make_exchange
from strategies.smc import ote

EXCHANGE = make_exchange()
CACHE_DIR = "data/bt_cache"
REFRESH = "--refresh" in sys.argv

FEE = 0.001          # 0.1% per side -> 0.2% round-trip, same as the other backtests
MAX_HOLD = 200       # bars to give a filled trade before the time-stop closes it
ENTRY_WINDOW = 20    # bars after the MSS to wait for the OTE limit to fill

REQUIRE_FVG = "--no-fvg" not in sys.argv
LONG_ONLY = "--long-only" in sys.argv
SHORT_ONLY = "--short-only" in sys.argv
# --- risk-management / target variants (the group's real method) ---
USE_PARTIAL = "--partial" in sys.argv   # bank 50% at TP1_R, move runner to break-even
TP1_R = 2.0                             # first take-profit in risk multiples (--tp1r=)
FIXED_R = None                          # --target=2r -> fixed 2R target instead of next-liquidity
# --htf=4h : alignment as an HTF-BIAS FILTER — keep the (working) LTF entry/stop/
# target, but drop any setup that fights the higher-timeframe market structure.
# --htf-strict also drops NEUTRAL-bias setups (require the HTF to actively agree).
HTF = None
HTF_STRICT = "--htf-strict" in sys.argv
SPLIT = None
TIMEFRAMES = ["4h", "1d"]
HISTORY = 8000
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
    if _a.startswith("--ote="):
        ote.OTE_FIB = float(_a.split("=", 1)[1])
    if _a.startswith("--tp1r="):
        TP1_R = float(_a.split("=", 1)[1])
    if _a.startswith("--target="):
        FIXED_R = float(_a.split("=", 1)[1].rstrip("rR"))
    if _a.startswith("--htf="):
        HTF = _a.split("=", 1)[1]
    if _a.startswith("--min-rr="):
        ote.MIN_RR = float(_a.split("=", 1)[1])
    if _a.startswith("--entry-window="):
        ENTRY_WINDOW = int(_a.split("=", 1)[1])


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


def _sim_ote(h, l, c, i, sig):
    """Two-stage OTE simulation. Returns (result, pnl_pct, risk_pct) for a filled
    trade, or None if the limit never filled (missed / expired unfilled).

    Target modes:
      default        the video's "next liquidity" target (sig['target'])
      FIXED_R        a fixed N-risk target (entry +/- N*risk), the closer TP
    Exit modes:
      single         first of stop / target (conservative on same-bar ties = loss)
      USE_PARTIAL    the group's risk mgmt: bank 50% at TP1_R, move the runner's
                     stop to break-even, runner exits at the full target
    """
    direction = sig["direction"]
    entry, stop, target = sig["entry"], sig["stop"], sig["target"]
    risk = abs(entry - stop)
    if FIXED_R is not None:             # override far liquidity target with a fixed-R TP
        target = entry + FIXED_R * risk if direction == "LONG" else entry - FIXED_R * risk
    tp1 = entry + TP1_R * risk if direction == "LONG" else entry - TP1_R * risk
    n = len(c)

    # ---- Stage 1: wait for the retrace to fill the OTE limit ----
    fill_k = None
    for k in range(i + 1, min(n, i + 1 + ENTRY_WINDOW)):
        if direction == "LONG":
            if h[k] >= target:          # ran to target before pulling back -> missed
                return None
            if l[k] <= entry:
                fill_k = k
                break
        else:
            if l[k] <= target:
                return None
            if h[k] >= entry:
                fill_k = k
                break
    if fill_k is None:
        return None                     # never retraced into the band

    risk_pct = risk / entry * 100.0
    end = min(n, fill_k + MAX_HOLD)

    def pct(px):
        return ((px - entry) if direction == "LONG" else (entry - px)) / entry * 100.0

    # ---- Stage 2a: single target vs stop (conservative on same-bar ties) ----
    if not USE_PARTIAL:
        for k in range(fill_k, end):
            hi, lo = h[k], l[k]
            if direction == "LONG":
                if lo <= stop:
                    return "LOSS", pct(stop) - FEE * 200, risk_pct
                if hi >= target:
                    return "WIN", pct(target) - FEE * 200, risk_pct
            else:
                if hi >= stop:
                    return "LOSS", pct(stop) - FEE * 200, risk_pct
                if lo <= target:
                    return "WIN", pct(target) - FEE * 200, risk_pct
        return "EXPIRED", pct(c[end - 1]) - FEE * 200, risk_pct

    # ---- Stage 2b: partial (bank 50% at TP1, runner to BE, then to target) ----
    banked = None                       # gross % from the 50% booked at TP1
    for k in range(fill_k, end):
        hi, lo = h[k], l[k]
        hit_stop = (lo <= stop) if direction == "LONG" else (hi >= stop)
        hit_tp1 = (hi >= tp1) if direction == "LONG" else (lo <= tp1)
        if banked is None:
            if hit_stop:                # stopped before TP1 -> full -1R (ties = loss)
                return "LOSS", pct(stop) - FEE * 200, risk_pct
            if hit_tp1:
                banked = 0.5 * pct(tp1)     # 50% booked; runner stop now = break-even
                continue
        else:
            hit_be = (lo <= entry) if direction == "LONG" else (hi >= entry)
            hit_tgt = (hi >= target) if direction == "LONG" else (lo <= target)
            if hit_be and not hit_tgt:  # runner scratched at break-even
                return "WIN", banked + 0.5 * 0.0 - FEE * 200, risk_pct
            if hit_tgt:
                return "WIN", banked + 0.5 * pct(target) - FEE * 200, risk_pct
    if banked is None:                  # never reached TP1
        return "EXPIRED", pct(c[end - 1]) - FEE * 200, risk_pct
    return "WIN", banked + 0.5 * pct(c[end - 1]) - FEE * 200, risk_pct


def _htf_bias_at(hi_idx, hi_px, lo_idx, lo_px, j, lb=2):
    """HTF market-structure bias at HTF bar j (no lookahead: only swings confirmed
    by j-lb). UP = higher high AND higher low; DOWN = lower high AND lower low."""
    bh = bisect.bisect_right(hi_idx, j - lb)
    bl = bisect.bisect_right(lo_idx, j - lb)
    if bh < 2 or bl < 2:
        return "NEUTRAL"
    if hi_px[bh - 1] > hi_px[bh - 2] and lo_px[bl - 1] > lo_px[bl - 2]:
        return "UP"
    if hi_px[bh - 1] < hi_px[bh - 2] and lo_px[bl - 1] < lo_px[bl - 2]:
        return "DOWN"
    return "NEUTRAL"


def _load_htf_bias(coin, htf):
    """Precompute (htf_timestamps, swing arrays) for the HTF-bias filter, or None."""
    hbars = get_history(coin, htf)
    if len(hbars) < 60:
        return None
    hdf = pd.DataFrame(hbars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    hh = hdf["high"].to_numpy(); hl = hdf["low"].to_numpy()
    hts = hdf["timestamp"].to_numpy().tolist()
    hi_idx, hi_px, lo_idx, lo_px = ote._confirmed_swings(hh, hl)
    return hts, hi_idx, hi_px, lo_idx, lo_px


def backtest_coin(coin):
    """Return a stats dict for one coin across all timeframes."""
    st = {"wins": 0, "losses": 0, "expired": 0, "pnl": 0.0,
          "win_pnl": 0.0, "loss_pnl": 0.0, "r_sum": 0.0, "n": 0,
          "setups": 0, "filled": 0, "htf_skip": 0, "bh_pnl": 0.0, "bh_n": 0}
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
        o = df["open"].to_numpy(); h = df["high"].to_numpy()
        l = df["low"].to_numpy(); c = df["close"].to_numpy()
        ts = df["timestamp"].to_numpy()

        if len(c) > 1 and c[0] > 0:            # buy-and-hold reality check
            st["bh_pnl"] += (c[-1] - c[0]) / c[0] * 100.0
            st["bh_n"] += 1

        htf = _load_htf_bias(coin, HTF) if (HTF and HTF != tf) else None

        for i, sig in ote.scan_ote(o, h, l, c, require_fvg=REQUIRE_FVG):
            if LONG_ONLY and sig["direction"] == "SHORT":
                continue
            if SHORT_ONLY and sig["direction"] == "LONG":
                continue
            # --- alignment: drop setups that fight the higher-timeframe draw ---
            if htf is not None:
                hts, hi_idx, hi_px, lo_idx, lo_px = htf
                j = bisect.bisect_right(hts, int(ts[i])) - 1   # last closed HTF bar
                bias = _htf_bias_at(hi_idx, hi_px, lo_idx, lo_px, j) if j >= 0 else "NEUTRAL"
                want = "UP" if sig["direction"] == "LONG" else "DOWN"
                opp = "DOWN" if sig["direction"] == "LONG" else "UP"
                if bias == opp or (HTF_STRICT and bias != want):
                    st["htf_skip"] += 1
                    continue
            st["setups"] += 1
            res = _sim_ote(h, l, c, i, sig)
            if res is None:
                continue                        # limit never filled -> not a trade
            st["filled"] += 1
            result, pnl, risk = res
            st["n"] += 1
            st["pnl"] += pnl
            st["r_sum"] += pnl / risk if risk else 0.0
            if result == "WIN":
                st["wins"] += 1; st["win_pnl"] += pnl
            elif result == "LOSS":
                st["losses"] += 1; st["loss_pnl"] += pnl
            else:
                st["expired"] += 1
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
    if REQUIRE_FVG: layers.append("displacement-FVG")
    if LONG_ONLY: layers.append("long-only")
    if SHORT_ONLY: layers.append("short-only")
    if FIXED_R is not None: layers.append(f"target={FIXED_R:g}R")
    else: layers.append("target=next-liquidity")
    if USE_PARTIAL: layers.append(f"partial-50%@{TP1_R:g}R+BE")
    if HTF: layers.append(f"htf-bias={HTF}{'/strict' if HTF_STRICT else ''}")
    print(f"OTE backtest | coins={len(coins)} | tf={TIMEFRAMES} | history={HISTORY}"
          f" | split={SPLIT or 'full'} | ote_fib={ote.OTE_FIB} | min_rr={ote.MIN_RR}"
          f" | entry_window={ENTRY_WINDOW}")
    print(f"  filters={layers}  |  fee modelled: {FEE*200:.1f}% round-trip\n")

    total = {"wins": 0, "losses": 0, "expired": 0, "pnl": 0.0,
             "win_pnl": 0.0, "loss_pnl": 0.0, "r_sum": 0.0, "n": 0,
             "setups": 0, "filled": 0, "htf_skip": 0, "bh_pnl": 0.0, "bh_n": 0}

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
    print("========== OTE RESULTS ==========")
    if HTF:
        print(f"HTF-bias dropped      : {total['htf_skip']} setups (fought {HTF} structure)")
    print(f"Setups found          : {total['setups']}")
    fill_rate = total["filled"] / total["setups"] * 100 if total["setups"] else 0
    print(f"Filled (limit hit)    : {total['filled']}  ({fill_rate:.0f}% of setups)")
    if n == 0:
        print("No filled OTE trades.")
        print("=================================")
        return
    win_rate = total["wins"] / resolved * 100 if resolved else 0
    avg = total["pnl"] / n
    avg_win = total["win_pnl"] / total["wins"] if total["wins"] else 0
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
