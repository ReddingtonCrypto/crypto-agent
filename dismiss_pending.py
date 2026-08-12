"""Review and dismiss setups that are still waiting for your approval.

The agent already retires a pending setup once price has run 30% of the whole
move, but that threshold is a blunt instrument. This is the manual version: it
shows every waiting setup with how far its move has already gone, so you can
clear out the ones that are simply too late to enter.

    python dismiss_pending.py                 list everything waiting
    python dismiss_pending.py --stale         dismiss the ones past the threshold
    python dismiss_pending.py --stale --pct 20   ... using your own threshold
    python dismiss_pending.py --id 471 473    dismiss these specific ones
    python dismiss_pending.py --all           clear the whole waiting list

Nothing is written unless you pass one of the dismiss options, and a dismissed
setup is marked CANCELLED — it never counted on the scoreboard, and it still
doesn't. Your open trades are never touched.
"""

import argparse
import sqlite3
import sys
import time

import paper_trading

DB = "database/crypto.db"


def _prices(coins):
    """Current price per coin. Falls back to an empty dict if the exchange is
    unreachable, in which case only --id and --all can be used."""
    try:
        import ccxt
        ex = ccxt.binance({"enableRateLimit": True})
        tk = ex.fetch_tickers(list(coins))
        return {c: float(t["last"]) for c, t in tk.items() if t.get("last")}
    except Exception as e:
        print(f"  (could not fetch prices: {type(e).__name__}) ")
        return {}


def _rows(conn):
    return conn.execute(
        "SELECT id, coin, direction, entry, stop, tp1, tp2, timeframe, strategy "
        "FROM paper_trades WHERE status='PENDING' ORDER BY id"
    ).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale", action="store_true",
                    help="dismiss setups whose move has already run past --pct")
    ap.add_argument("--pct", type=float, default=30.0,
                    help="how much of the whole move counts as too late (default 30)")
    ap.add_argument("--id", type=int, nargs="+", help="dismiss these ids")
    ap.add_argument("--all", action="store_true", help="dismiss every waiting setup")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    rows = _rows(conn)
    if not rows:
        print("Nothing is waiting for approval.")
        return

    prices = _prices({r[1] for r in rows})

    print(f"{'id':>5}  {'coin':<12} {'tf':<4} {'strategy':<9} {'dir':<5} "
          f"{'entry':>11} {'now':>11} {'moved':>8}  state")
    print("-" * 82)
    stale = []
    for r in rows:
        tid, coin, direction, entry, stop, tp1, tp2, tf, strat = r
        px = prices.get(coin)
        moved, invalid = paper_trading.pending_progress(r, px)
        if invalid:
            state, pct = "INVALID — stop already hit", ""
            stale.append(tid)
        elif moved is None:
            state, pct = "no price", ""
        else:
            pct = f"{moved * 100:6.1f}%"
            if moved >= args.pct / 100:
                state = "TOO LATE"
                stale.append(tid)
            elif moved < 0:
                state = "still ahead of entry"
            else:
                state = "ok"
        print(f"{tid:>5}  {coin:<12} {tf:<4} {strat:<9} {direction:<5} "
              f"{entry:>11.6g} {(px if px else 0):>11.6g} {pct:>8}  {state}")

    if args.all:
        targets = [r[0] for r in rows]
    elif args.id:
        targets = [i for i in args.id if i in {r[0] for r in rows}]
        missing = set(args.id) - set(targets)
        if missing:
            print(f"\nnot waiting (already approved, declined or gone): "
                  f"{sorted(missing)}")
    elif args.stale:
        targets = stale
    else:
        print(f"\n{len(stale)} of {len(rows)} look too late. "
              f"Re-run with --stale to dismiss them, or --id <n> for specific ones.")
        return

    if not targets:
        print("\nNothing to dismiss.")
        return

    now = time.time()
    conn.executemany(
        "UPDATE paper_trades SET status='CANCELLED', closed_at=? "
        "WHERE id=? AND status='PENDING'",
        [(now, t) for t in targets],
    )
    conn.commit()
    print(f"\nDismissed {len(targets)} setup(s): {targets}")
    print("They were never trades and never counted — they are now closed off.")


if __name__ == "__main__":
    main()
