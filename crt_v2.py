"""CRT v2 — the FAITHFUL timeframe-aligned Candle Range Theory engine.

Built to the full 3-part spec. Unlike crt_backtest.py (a single-TF stepping
stone), this marks the setup on the HIGHER timeframe and executes the entry on
the aligned LOWER timeframe — the way the strategy is actually taught.

Read-only lab tool. Reuses the shared candle cache, fee model and universe.
Nothing here touches the live bot or the DB.

THE MODEL
=========
HTF (mark) — Monthly / Weekly / Daily:
  * Trend  = market structure: Higher-High+Higher-Low = UP, LH+LL = DOWN
             (via market_structure.find_swings). Trade WITH the trend only.
  * CRT    = C1 range -> C2 sweeps C1's high (bearish) or low (bullish) AND
             C2's body closes back inside C1's range. Target = C1 BODY
             (min/max of C1 open/close). HTF invalidation = C2's swept extreme.
  * Key level (need >= 1): OLD HIGH/LOW (C2 swept a prior swing extreme),
             FVG (unfilled gap at the setup), REJECTION BLOCK (failed CISD).
  * TBS/TWS tagged on the sweep (body-close-through vs wick-only).

LTF (execute) — aligned pair (Weekly->H4, Daily->H1, Monthly->Daily):
  * Sweep of a recent LTF high/low in the trade direction, then a
    SINGLE-CANDLE CISD close (close beyond the sweeping candle's body).
  * Entry at that close. SL just beyond the swept LTF extreme (tight).
    TP = the HTF C1 body.

FLAGS
  --kl=oldhl,fvg,rejblock   which HTF key levels count (default all)
  --tbs-only                require a body-close sweep (A+) on the LTF entry
  --long-only / --short-only
  --split=first/last        walk-forward
  --pairs=1w:4h,1d:1h        override the alignment pairs
  --top=N --history=N --ltf-history=N --jobs=N --refresh

Run: python crt_v2.py --top=40 --history=8000 --ltf-history=30000 --jobs=4
"""

import bisect
import json
import os
import sys

import pandas as pd

import universe
from data_source import make_exchange
from strategies.smc.market_structure import find_swings

EXCHANGE = make_exchange()
CACHE_DIR = "data/bt_cache"
REFRESH = "--refresh" in sys.argv

FEE = 0.001
MAX_HOLD = 300         # LTF bars to give a trade before the time-stop
SWING_LB = 2           # swing lookback (bars each side) for structure/old-h-l
KL_LOOKBACK = 20       # HTF bars back for the old-high/low sweep + FVG search
LTF_SWEEP_LB = 20      # LTF bars back that the entry sweep must exceed
CISD_WINDOW = 4        # LTF bars after the sweep to allow the CISD close (A+ = fast)
ALIGN_WINDOW = 10      # HTF bars after C2 to keep hunting for the LTF entry

LONG_ONLY = "--long-only" in sys.argv
SHORT_ONLY = "--short-only" in sys.argv
TBS_ONLY = "--tbs-only" in sys.argv
SPLIT = None
HISTORY = 8000         # HTF candles
LTF_HISTORY = 30000    # LTF candles (deep, so walk-forward has coverage)
TOP_N = 40
JOBS = 1
PAIRS = [("1w", "4h"), ("1d", "1h")]   # Weekly->H4, Daily->H1 (safe set)
KL_TYPES = {"oldhl", "fvg", "rejblock"}
for _a in sys.argv:
    if _a.startswith("--split="):
        SPLIT = _a.split("=", 1)[1]
    elif _a.startswith("--history="):
        HISTORY = int(_a.split("=", 1)[1])
    elif _a.startswith("--ltf-history="):
        LTF_HISTORY = int(_a.split("=", 1)[1])
    elif _a.startswith("--top="):
        TOP_N = int(_a.split("=", 1)[1])
    elif _a.startswith("--jobs="):
        JOBS = int(_a.split("=", 1)[1])
    elif _a.startswith("--kl="):
        KL_TYPES = set(_a.split("=", 1)[1].split(","))
    elif _a.startswith("--pairs="):
        PAIRS = [tuple(p.split(":")) for p in _a.split("=", 1)[1].split(",")]


def get_history(coin, timeframe, limit):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{coin.replace('/', '_')}_{timeframe}.json")
    if not REFRESH and os.path.exists(path):
        with open(path) as f:
            bars = json.load(f)
        if len(bars) >= limit or len(bars) >= 900:   # cache good enough
            return bars
    if limit <= 1000:
        bars = EXCHANGE.fetch_ohlcv(coin, timeframe, limit=limit)
    else:
        tf_ms = EXCHANGE.parse_timeframe(timeframe) * 1000
        since = max(0, EXCHANGE.milliseconds() - limit * tf_ms)
        bars = []
        while len(bars) < limit:
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


def _confirmed_swings(df):
    """Swing highs/lows as (index, price), each only 'known' SWING_LB bars later
    (so using it at bar i with idx <= i-SWING_LB has no look-ahead)."""
    return find_swings(df, lookback=SWING_LB)


def _trend_at(hi_sw, lo_sw, i):
    """Market-structure trend visible at bar i: UP if the last two confirmed
    swing highs AND lows are both rising, DOWN if both falling, else None."""
    hs = [p for (idx, p) in hi_sw if idx <= i - SWING_LB]
    ls = [p for (idx, p) in lo_sw if idx <= i - SWING_LB]
    if len(hs) < 2 or len(ls) < 2:
        return None
    if hs[-1] > hs[-2] and ls[-1] > ls[-2]:
        return "LONG"
    if hs[-1] < hs[-2] and ls[-1] < ls[-2]:
        return "SHORT"
    return None


def _has_fvg(o, h, l, c, i, direction):
    """Matching-direction unfilled 3-candle FVG whose zone the C2 extreme taps."""
    swept = l[i] if direction == "LONG" else h[i]
    start = max(2, i - KL_LOOKBACK)
    for k in range(i, start - 1, -1):
        a = k - 2
        if a < 0:
            break
        if direction == "LONG" and h[a] < l[k] and h[a] <= swept <= l[k]:
            return True
        if direction == "SHORT" and l[a] > h[k] and h[k] <= swept <= l[a]:
            return True
    return False


def _old_level(hi_sw, lo_sw, i, direction, h, l, c):
    """OLD HIGH/LOW key level: C2 swept a PRIOR SWING extreme (a real old high/
    low), and the CRT then formed (C2 closed back in). Returns the swept level
    or None. TBS/TWS is judged against this level."""
    if direction == "SHORT":
        cands = [p for (idx, p) in hi_sw if idx <= i - SWING_LB and idx >= i - KL_LOOKBACK]
        cands = [p for p in cands if p < h[i]]        # C2 wicked above it
        if cands:
            return max(cands)                          # nearest old high below the wick
    else:
        cands = [p for (idx, p) in lo_sw if idx <= i - SWING_LB and idx >= i - KL_LOOKBACK]
        cands = [p for p in cands if p > l[i]]
        if cands:
            return min(cands)
    return None


def _has_rejblock(o, h, l, c, i, direction):
    """Rejection Block = failed CISD (approximation): a nearby level that was
    tested by WICK at least twice within the lookback but never BODY-closed
    through, and the CRT is forming at it. Bullish rej-block = a low repeatedly
    wicked but held (support); bearish = a high wicked but held (resistance)."""
    start = max(SWING_LB, i - KL_LOOKBACK)
    if direction == "LONG":
        lvl = l[i]
        wick_tests = sum(1 for j in range(start, i)
                         if l[j] <= lvl * 1.005 and min(o[j], c[j]) > lvl)
        return wick_tests >= 2
    else:
        lvl = h[i]
        wick_tests = sum(1 for j in range(start, i)
                         if h[j] >= lvl * 0.995 and max(o[j], c[j]) < lvl)
        return wick_tests >= 2


def _ltf_entry(o, h, l, c, ts, start_idx, end_ts, direction):
    """Taught LTF entry: a sweep of a recent LTF extreme, then a SINGLE-CANDLE
    CISD close beyond the sweeping candle's body within CISD_WINDOW bars.
    Returns (enter_idx, entry, stop, is_tbs) or None."""
    n = len(c)
    j = max(start_idx, LTF_SWEEP_LB)
    while j < n - 1 and ts[j] < end_ts:
        if direction == "SHORT":
            old_hi = h[j - LTF_SWEEP_LB:j].max()
            if h[j] > old_hi:                                  # swept a recent high
                is_tbs = c[j] > old_hi                         # body closed beyond = TBS
                body_lo = min(o[j], c[j])
                for k in range(j + 1, min(j + 1 + CISD_WINDOW, n)):
                    if c[k] < body_lo:                         # single-candle CISD down
                        return k, c[k], h[j], is_tbs
        else:
            old_lo = l[j - LTF_SWEEP_LB:j].min()
            if l[j] < old_lo:
                is_tbs = c[j] < old_lo
                body_hi = max(o[j], c[j])
                for k in range(j + 1, min(j + 1 + CISD_WINDOW, n)):
                    if c[k] > body_hi:
                        return k, c[k], l[j], is_tbs
        j += 1
    return None


def _simulate(highs, lows, closes, i, direction, entry, stop, target):
    risk_pct = abs(entry - stop) / entry * 100.0 if entry else 0
    end = min(len(highs), i + 1 + MAX_HOLD)
    for k in range(i + 1, end):
        hi, lo = highs[k], lows[k]
        if direction == "LONG":
            if lo <= stop:
                return "LOSS", (stop - entry) / entry * 100 - FEE * 200, risk_pct
            if hi >= target:
                return "WIN", (target - entry) / entry * 100 - FEE * 200, risk_pct
        else:
            if hi >= stop:
                return "LOSS", (entry - stop) / entry * 100 - FEE * 200, risk_pct
            if lo <= target:
                return "WIN", (entry - target) / entry * 100 - FEE * 200, risk_pct
    last = closes[end - 1]
    g = ((last - entry) if direction == "LONG" else (entry - last)) / entry * 100
    return "EXPIRED", g - FEE * 200, risk_pct


def _blank():
    return {"wins": 0, "losses": 0, "expired": 0, "pnl": 0.0,
            "win_pnl": 0.0, "loss_pnl": 0.0, "n": 0, "tbs": 0,
            "bh_pnl": 0.0, "bh_n": 0}


def backtest_coin(coin):
    st = _blank()
    pairs = [(htf, ltf) for (htf, ltf) in PAIRS]
    for htf, ltf in pairs:
        hbars = get_history(coin, htf, HISTORY)
        if len(hbars) > HISTORY:
            hbars = hbars[-HISTORY:]
        if SPLIT == "first":
            hbars = hbars[: len(hbars) // 2]
        elif SPLIT == "last":
            hbars = hbars[len(hbars) // 2:]
        lbars = get_history(coin, ltf, LTF_HISTORY)
        if len(hbars) < 120 or len(lbars) < 120:
            continue

        hdf = pd.DataFrame(hbars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        ho = hdf["open"].to_numpy(); hh = hdf["high"].to_numpy()
        hl = hdf["low"].to_numpy(); hc = hdf["close"].to_numpy()
        hts = hdf["timestamp"].to_numpy()
        hi_sw, lo_sw = _confirmed_swings(hdf)
        htf_ms = EXCHANGE.parse_timeframe(htf) * 1000

        ldf = pd.DataFrame(lbars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        lo_ = ldf["open"].to_numpy(); lh = ldf["high"].to_numpy()
        ll = ldf["low"].to_numpy(); lc = ldf["close"].to_numpy()
        lts = ldf["timestamp"].to_numpy().tolist()

        if hc[-1] > 0 and hc[0] > 0:
            st["bh_pnl"] += (hc[-1] - hc[0]) / hc[0] * 100.0
            st["bh_n"] += 1

        for i in range(KL_LOOKBACK + 1, len(hdf) - 1):
            c1_hi, c1_lo, c1_o, c1_c = hh[i - 1], hl[i - 1], ho[i - 1], hc[i - 1]
            swept_high = hh[i] > c1_hi
            swept_low = hl[i] < c1_lo
            if not (c1_lo <= hc[i] <= c1_hi) or swept_high == swept_low:
                continue
            direction = "SHORT" if swept_high else "LONG"
            target = min(c1_o, c1_c) if swept_high else max(c1_o, c1_c)
            if LONG_ONLY and direction == "SHORT":
                continue
            if SHORT_ONLY and direction == "LONG":
                continue

            # --- HTF trend (market structure) ---
            if _trend_at(hi_sw, lo_sw, i) != direction:
                continue

            # --- HTF key level (>=1 enabled type) ---
            at_kl = False
            if "oldhl" in KL_TYPES and _old_level(hi_sw, lo_sw, i, direction, hh, hl, hc) is not None:
                at_kl = True
            if not at_kl and "fvg" in KL_TYPES and _has_fvg(ho, hh, hl, hc, i, direction):
                at_kl = True
            if not at_kl and "rejblock" in KL_TYPES and _has_rejblock(ho, hh, hl, hc, i, direction):
                at_kl = True
            if not at_kl:
                continue

            # --- drop to aligned LTF for the entry ---
            t2 = int(hts[i]) + htf_ms
            if not lts or t2 < lts[0] or t2 > lts[-1]:
                continue
            start_idx = bisect.bisect_left(lts, t2)
            res = _ltf_entry(lo_, lh, ll, lc, lts, start_idx,
                             t2 + ALIGN_WINDOW * htf_ms, direction)
            if res is None:
                continue
            enter_idx, entry, stop, is_tbs = res
            if TBS_ONLY and not is_tbs:
                continue
            if direction == "LONG" and target <= entry:
                continue
            if direction == "SHORT" and target >= entry:
                continue

            result, pnl, risk = _simulate(lh, ll, lc, enter_idx, direction, entry, stop, target)
            st["n"] += 1
            st["pnl"] += pnl
            if is_tbs:
                st["tbs"] += 1
            if result == "WIN":
                st["wins"] += 1; st["win_pnl"] += pnl
            elif result == "LOSS":
                st["losses"] += 1; st["loss_pnl"] += pnl
            else:
                st["expired"] += 1
                (st.__setitem__("win_pnl", st["win_pnl"] + pnl) if pnl > 0
                 else st.__setitem__("loss_pnl", st["loss_pnl"] + pnl))
    return st


def main():
    try:
        coins = universe.get_universe(EXCHANGE, 100)[:TOP_N]
    except Exception as e:
        print(f"universe unavailable ({type(e).__name__}); majors fallback")
        coins = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
                 "AVAX/USDT", "LINK/USDT", "LTC/USDT", "DOT/USDT", "DOGE/USDT"]

    tags = []
    if LONG_ONLY: tags.append("long-only")
    if SHORT_ONLY: tags.append("short-only")
    if TBS_ONLY: tags.append("TBS-only")
    print(f"CRT v2 (aligned) | coins={len(coins)} | pairs={['->'.join(p) for p in PAIRS]}"
          f" | kl={sorted(KL_TYPES)} | split={SPLIT or 'full'} | {tags or ['both']}")
    print(f"HTF hist={HISTORY} LTF hist={LTF_HISTORY} | fee {FEE*200:.1f}% round-trip\n")

    total = _blank()

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
    print("========== CRT v2 (ALIGNED) RESULTS ==========")
    if n == 0:
        print("No CRT setups found.")
        print("==============================================")
        return
    wr = total["wins"] / resolved * 100 if resolved else 0
    avg = total["pnl"] / n
    aw = total["win_pnl"] / total["wins"] if total["wins"] else 0
    al = total["loss_pnl"] / total["losses"] if total["losses"] else 0
    bh = total["bh_pnl"] / total["bh_n"] if total["bh_n"] else 0
    print(f"Trades (incl expired): {n}   (TBS body-sweeps: {total['tbs']})")
    print(f"  wins={total['wins']} losses={total['losses']} expired={total['expired']}")
    print(f"Win rate (resolved)  : {wr:.1f}%")
    print(f"Avg P&L / trade       : {avg:+.2f}%   (after fees)")
    print(f"Total P&L             : {total['pnl']:+.1f}%")
    print(f"Avg winner / loser    : +{aw:.2f}% / {al:.2f}%")
    print(f"Buy-and-hold benchmark: {bh:+.1f}% avg per coin (HTF window)")
    print("==============================================")


if __name__ == "__main__":
    main()
