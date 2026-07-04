"""Strategy Health Monitor — the ADAPTIVE, learnable layer.

Reads realized paper-trade results (the honest WIN/LOSS data) over a trailing
window and decides which coins/strategies are currently healthy enough to keep
trading. It is deliberately CONSERVATIVE-ONLY: it can pause a decaying setup and
later re-enable it, but it never invents new parameters or loosens risk — so it
adapts without the overfitting that kills auto-optimizers.

Recovery is automatic: health is measured over a trailing CALENDAR window, so a
paused coin's bad trades age out of the window over time, making it eligible to
be probed again — no manual reset needed.

This is the dynamic counterpart to the static blacklist in universe.py: the
blacklist came from a one-off backtest; this reacts to what's happening live.
"""

import sqlite3


DB = "database/crypto.db"

HEALTH_WINDOW_DAYS = 21   # trailing window of realized trades to judge on
MIN_TRADES = 6            # need this many closed trades before we'll pause a coin
PAUSE_THRESHOLD = -0.2    # avg P&L %/trade over the window below which -> PAUSED


def _conn():
    return sqlite3.connect(DB)


def coin_health():
    """Per-coin health over the trailing window: {coin: {trades, win_rate,
    avg_pnl, status}}. status is 'PAUSED' only with enough evidence of decay."""
    conn = _conn()
    rows = conn.execute(
        """
        SELECT coin,
               COUNT(*),
               SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END),
               AVG(pnl_pct)
        FROM paper_trades
        WHERE status IN ('WIN','LOSS')
          AND closed_at >= datetime('now', ?)
        GROUP BY coin
        """,
        (f"-{HEALTH_WINDOW_DAYS} days",),
    ).fetchall()
    conn.close()

    out = {}
    for coin, n, wins, avg in rows:
        avg = avg or 0.0
        paused = n >= MIN_TRADES and avg < PAUSE_THRESHOLD
        out[coin] = {
            "trades": n,
            "win_rate": round(wins / n * 100, 1) if n else 0.0,
            "avg_pnl": round(avg, 2),
            "status": "PAUSED" if paused else "ACTIVE",
        }
    return out


def paused_coins():
    """Set of coins currently paused for poor recent expectancy."""
    return {c for c, h in coin_health().items() if h["status"] == "PAUSED"}


def strategy_health():
    """Same idea per strategy (for reporting) over the trailing window."""
    conn = _conn()
    rows = conn.execute(
        """
        SELECT strategy,
               COUNT(*),
               SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END),
               AVG(pnl_pct)
        FROM paper_trades
        WHERE status IN ('WIN','LOSS')
          AND strategy IS NOT NULL
          AND closed_at >= datetime('now', ?)
        GROUP BY strategy
        """,
        (f"-{HEALTH_WINDOW_DAYS} days",),
    ).fetchall()
    conn.close()

    out = []
    for strat, n, wins, avg in rows:
        avg = avg or 0.0
        out.append({
            "strategy": strat,
            "trades": n,
            "win_rate": round(wins / n * 100, 1) if n else 0.0,
            "avg_pnl": round(avg, 2),
        })
    out.sort(key=lambda x: x["avg_pnl"], reverse=True)
    return out
