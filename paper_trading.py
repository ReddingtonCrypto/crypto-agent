"""Paper-trading tracker: turns each signal into a pretend trade and checks,
on every scan, whether price hit the target (win) or the stop (loss). This is
how we measure whether the signals actually work - no real money involved.
"""

import sqlite3
from datetime import datetime, timezone


DB = "database/crypto.db"


def _conn():
    return sqlite3.connect(DB)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def has_open_trade(coin, direction):
    conn = _conn()
    row = conn.execute(
        "SELECT 1 FROM paper_trades WHERE coin=? AND direction=? AND status='OPEN' LIMIT 1",
        (coin, direction),
    ).fetchone()
    conn.close()
    return row is not None


def open_trade(coin, direction, entry, stop, tp1, tp2, score):
    """Open a paper trade, unless one for this coin+direction is already open.
    Returns True if a new trade was opened."""
    if has_open_trade(coin, direction):
        return False

    conn = _conn()
    conn.execute(
        """
        INSERT INTO paper_trades
        (coin, direction, entry, stop, tp1, tp2, score, status, opened_at)
        VALUES (?,?,?,?,?,?,?, 'OPEN', ?)
        """,
        (coin, direction, entry, stop, tp1, tp2, score, _now()),
    )
    conn.commit()
    conn.close()
    return True


def update_open_trades(price_map):
    """Check every open trade against the latest price. Close it as WIN if it
    reached TP1, or LOSS if it hit the stop. `price_map` is {coin: price}."""
    conn = _conn()
    rows = conn.execute(
        "SELECT id, coin, direction, entry, stop, tp1 FROM paper_trades WHERE status='OPEN'"
    ).fetchall()

    closed = []
    for tid, coin, direction, entry, stop, tp1 in rows:
        price = price_map.get(coin)
        if price is None or not entry:  # skip missing price or bad (zero) entry
            continue

        outcome = None
        exit_price = None

        if direction == "LONG":
            if price <= stop:
                outcome, exit_price = "LOSS", stop
            elif price >= tp1:
                outcome, exit_price = "WIN", tp1
        else:  # SHORT
            if price >= stop:
                outcome, exit_price = "LOSS", stop
            elif price <= tp1:
                outcome, exit_price = "WIN", tp1

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
        "win_rate": win_rate,
        "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else 0.0,
    }
