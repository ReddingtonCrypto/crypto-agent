"""Paper-trading tracker: turns each signal into a pretend trade and checks,
on every scan, whether price hit the target (win) or the stop (loss). This is
how we measure whether the signals actually work - no real money involved.
"""

import sqlite3
from datetime import datetime, timezone


DB = "database/crypto.db"

# Time-stop: a trade that hasn't hit TP1 or its stop within MAX_HOLD_BARS bars
# of its own timeframe is closed as EXPIRED at the current price. This mirrors
# the backtester's MAX_HOLD drop so the live scoreboard measures the same thing
# — without it, meandering trades sit OPEN forever and never resolve, quietly
# skewing what the dashboard reports.
MAX_HOLD_BARS = 200
TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720,
    "1d": 1440, "1w": 10080,
}


def _conn():
    return sqlite3.connect(DB)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse(ts):
    """Parse a stored UTC timestamp string back to an aware datetime."""
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _expired(opened_at, timeframe):
    """True once a trade has been open longer than MAX_HOLD_BARS of its TF."""
    if not opened_at:
        return False
    minutes = TF_MINUTES.get(timeframe or "1h", 60) * MAX_HOLD_BARS
    age_min = (datetime.now(timezone.utc) - _parse(opened_at)).total_seconds() / 60.0
    return age_min >= minutes


def has_open_trade(coin, direction, timeframe, strategy):
    conn = _conn()
    row = conn.execute(
        "SELECT 1 FROM paper_trades WHERE coin=? AND direction=? AND timeframe=? "
        "AND strategy=? AND status='OPEN' LIMIT 1",
        (coin, direction, timeframe, strategy),
    ).fetchone()
    conn.close()
    return row is not None


def open_trade(coin, direction, entry, stop, tp1, tp2, score, timeframe, strategy):
    """Open a paper trade, unless one for this coin+direction+timeframe+strategy
    is already open. Returns True if a new trade was opened."""
    if has_open_trade(coin, direction, timeframe, strategy):
        return False

    conn = _conn()
    conn.execute(
        """
        INSERT INTO paper_trades
        (coin, direction, entry, stop, tp1, tp2, score, timeframe, strategy, status, opened_at)
        VALUES (?,?,?,?,?,?,?,?,?, 'OPEN', ?)
        """,
        (coin, direction, entry, stop, tp1, tp2, score, timeframe, strategy, _now()),
    )
    conn.commit()
    conn.close()
    return True


PARTIAL_FRAC = 0.5   # fraction of the position banked at TP1 (rest runs to TP2)


def _leg(entry, exit_price, direction):
    """Signed % return of one exit level, favourable-positive for the trade."""
    r = (exit_price - entry) / entry * 100.0
    return -r if direction == "SHORT" else r


def update_open_trades(bars, trend_flipped=None):
    """Advance every open trade against the latest candle, running the backtested
    partial-exit plan (Variant C):

      1. Bank PARTIAL_FRAC of the position at TP1 (=2R) and move the runner's
         stop to break-even. The trade stays OPEN.
      2. Close the runner at TP2 (=4R), at break-even, or via the time-stop.

    Final P&L blends the banked half and the runner. Returns a list of events
    (partial banks and full closes) for Telegram. `bars` is {coin: {high,low,price}}.
    """
    conn = _conn()
    rows = conn.execute(
        "SELECT id, coin, direction, entry, stop, tp1, tp2, timeframe, strategy, "
        "opened_at, tp1_hit, realized_pct FROM paper_trades WHERE status='OPEN'"
    ).fetchall()

    events = []
    for (tid, coin, direction, entry, stop, tp1, tp2, timeframe, strategy,
         opened_at, tp1_hit, realized_pct) in rows:
        bar = bars.get(coin)
        if bar is None or not entry:  # skip missing data or bad (zero) entry
            continue

        # Intraday high/low so we don't miss a level hit then retraced.
        hi, lo = bar["high"], bar["low"]
        realized_pct = realized_pct or 0.0

        # ---- Trend-following: exit on trend flip or the wide catastrophic stop
        #      (no Variant C partials — let the trend run). ----
        if strategy == "TrendMA":
            flipped = trend_flipped and (coin, timeframe) in trend_flipped
            exit_price = None
            if direction == "LONG" and lo <= stop:
                exit_price = stop
            elif direction == "SHORT" and hi >= stop:
                exit_price = stop
            elif flipped:
                exit_price = bar["price"]
            if exit_price is not None:
                pnl = round(_leg(entry, exit_price, direction), 2)
                result = "WIN" if pnl > 0 else "LOSS"
                conn.execute(
                    "UPDATE paper_trades SET status=?, closed_at=?, exit_price=?, pnl_pct=? WHERE id=?",
                    (result, _now(), exit_price, pnl, tid),
                )
                events.append({
                    "coin": coin, "direction": direction, "result": result,
                    "pnl_pct": pnl, "timeframe": timeframe, "strategy": strategy,
                })
            continue

        # ---- Phase 1: partial not yet banked ----
        if not tp1_hit:
            outcome = exit_price = None
            if direction == "LONG":
                if lo <= stop:                 # stop before TP1 -> full loss
                    outcome, exit_price = "LOSS", stop
                elif hi >= tp1:                # bank the partial, arm the runner
                    outcome = "TP1"
            else:
                if hi >= stop:
                    outcome, exit_price = "LOSS", stop
                elif lo <= tp1:
                    outcome = "TP1"

            if outcome == "TP1":
                # Lock in the banked half; move the runner's stop to break-even.
                banked = round(PARTIAL_FRAC * _leg(entry, tp1, direction), 4)
                conn.execute(
                    "UPDATE paper_trades SET tp1_hit=1, realized_pct=?, stop=? WHERE id=?",
                    (banked, entry, tid),
                )
                events.append({
                    "coin": coin, "direction": direction, "result": "TP1",
                    "pnl_pct": round(banked, 2), "timeframe": timeframe, "strategy": strategy,
                })
                continue

            if outcome is None and _expired(opened_at, timeframe):
                outcome, exit_price = "EXPIRED", bar["price"]

            if outcome:  # full LOSS or EXPIRED before any partial
                pnl = round(_leg(entry, exit_price, direction), 2)
                conn.execute(
                    "UPDATE paper_trades SET status=?, closed_at=?, exit_price=?, pnl_pct=? WHERE id=?",
                    (outcome, _now(), exit_price, pnl, tid),
                )
                events.append({
                    "coin": coin, "direction": direction, "result": outcome,
                    "pnl_pct": pnl, "timeframe": timeframe, "strategy": strategy,
                })
            continue

        # ---- Phase 2: runner active (partial already banked, stop = entry) ----
        exit_price = None
        if direction == "LONG":
            if lo <= stop:              # break-even stop
                exit_price = stop
            elif hi >= tp2:
                exit_price = tp2
        else:
            if hi >= stop:
                exit_price = stop
            elif lo <= tp2:
                exit_price = tp2

        if exit_price is None and _expired(opened_at, timeframe):
            exit_price = bar["price"]   # time-stop the runner at current price

        if exit_price is not None:
            total = realized_pct + (1 - PARTIAL_FRAC) * _leg(entry, exit_price, direction)
            total = round(total, 2)
            result = "WIN" if total > 0 else "LOSS"
            conn.execute(
                "UPDATE paper_trades SET status=?, closed_at=?, exit_price=?, pnl_pct=? WHERE id=?",
                (result, _now(), exit_price, total, tid),
            )
            events.append({
                "coin": coin, "direction": direction, "result": result,
                "pnl_pct": total, "timeframe": timeframe, "strategy": strategy,
            })

    conn.commit()
    conn.close()
    return events  # partial-bank + full-close events


def get_stats():
    conn = _conn()
    open_count = conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status='OPEN'"
    ).fetchone()[0]
    wins = conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status='WIN'"
    ).fetchone()[0]
    losses = conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status='LOSS'"
    ).fetchone()[0]
    expired = conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status='EXPIRED'"
    ).fetchone()[0]
    avg_pnl = conn.execute(
        "SELECT AVG(pnl_pct) FROM paper_trades WHERE status IN ('WIN','LOSS')"
    ).fetchone()[0]
    conn.close()

    closed = wins + losses
    win_rate = round(wins / closed * 100, 1) if closed else 0.0

    return {
        "open": open_count,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "expired": expired,
        "win_rate": win_rate,
        "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else 0.0,
    }


def open_counts_by_direction():
    """How many open trades are currently LONG vs SHORT."""
    conn = _conn()
    rows = conn.execute(
        "SELECT direction, COUNT(*) FROM paper_trades WHERE status='OPEN' GROUP BY direction"
    ).fetchall()
    conn.close()
    return {d: n for d, n in rows}


def get_stats_by_strategy():
    """Same scoreboard, broken down per strategy (Trend / Range / ICT) so you
    can see which one is actually winning."""
    conn = _conn()
    strategies = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT strategy FROM paper_trades WHERE strategy IS NOT NULL"
        ).fetchall()
    ]
    out = []
    for strat in strategies:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE strategy=? AND status='OPEN'", (strat,)
        ).fetchone()[0]
        wins = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE strategy=? AND status='WIN'", (strat,)
        ).fetchone()[0]
        losses = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE strategy=? AND status='LOSS'", (strat,)
        ).fetchone()[0]
        avg_pnl = conn.execute(
            "SELECT AVG(pnl_pct) FROM paper_trades WHERE strategy=? AND status IN ('WIN','LOSS')",
            (strat,),
        ).fetchone()[0]
        done = wins + losses
        out.append({
            "strategy": strat,
            "open": open_count,
            "closed": done,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / done * 100, 1) if done else 0.0,
            "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else 0.0,
        })
    conn.close()
    out.sort(key=lambda x: x["avg_pnl"], reverse=True)
    return out
