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


def update_open_trades(bars):
    """Check every open trade against the latest price. Close it as WIN if it
    reached TP1, or LOSS if it hit the stop. `price_map` is {coin: price}."""
    conn = _conn()
    rows = conn.execute(
        "SELECT id, coin, direction, entry, stop, tp1, timeframe, strategy, opened_at "
        "FROM paper_trades WHERE status='OPEN'"
    ).fetchall()

    closed = []
    for tid, coin, direction, entry, stop, tp1, timeframe, strategy, opened_at in rows:
        bar = bars.get(coin)
        if bar is None or not entry:  # skip missing data or bad (zero) entry
            continue

        # Check the candle's HIGH and LOW (intraday), not just the close, so
        # we don't miss a target/stop that was hit and then retraced.
        hi = bar["high"]
        lo = bar["low"]

        outcome = None
        exit_price = None

        if direction == "LONG":
            if lo <= stop:
                outcome, exit_price = "LOSS", stop
            elif hi >= tp1:
                outcome, exit_price = "WIN", tp1
        else:  # SHORT
            if hi >= stop:
                outcome, exit_price = "LOSS", stop
            elif lo <= tp1:
                outcome, exit_price = "WIN", tp1

        # Time-stop: neither target nor stop hit within the hold window ->
        # close at the current price and mark EXPIRED (kept out of win-rate,
        # mirroring the backtester which drops these).
        if outcome is None and _expired(opened_at, timeframe):
            outcome, exit_price = "EXPIRED", bar["price"]

        if outcome:
            pnl = (exit_price - entry) / entry * 100.0
            if direction == "SHORT":
                pnl = -pnl
            pnl = round(pnl, 2)
            conn.execute(
                "UPDATE paper_trades SET status=?, closed_at=?, exit_price=?, pnl_pct=? WHERE id=?",
                (outcome, _now(), exit_price, pnl, tid),
            )
            closed.append({
                "coin": coin,
                "direction": direction,
                "result": outcome,
                "pnl_pct": pnl,
                "timeframe": timeframe,
                "strategy": strategy,
            })

    conn.commit()
    conn.close()
    return closed  # list of closed-trade dicts


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
