"""Generate a single static dashboard page (site/index.html) from the database.

Shows the accuracy scoreboard, open paper trades, and recent results.
Published free via GitHub Pages, viewable on your phone.
"""

import os
import sqlite3
from datetime import datetime, timezone

from formatting import fmt_price
import health_monitor


DB = "database/crypto.db"
OUT_DIR = "site"
OUT_FILE = os.path.join(OUT_DIR, "index.html")


CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px;
  background: #0d1117; color: #e6edf3;
  font-family: -apple-system, Segoe UI, Roboto, sans-serif;
}
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: #8b949e; font-size: 12px; margin-bottom: 16px; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px; }
.card {
  background: #161b22; border: 1px solid #30363d; border-radius: 10px;
  padding: 12px 16px; min-width: 90px; flex: 1;
}
.card .label { color: #8b949e; font-size: 11px; text-transform: uppercase; }
.card .value { font-size: 22px; font-weight: 700; margin-top: 4px; }
h2 { font-size: 15px; margin: 20px 0 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid #21262d; }
th { color: #8b949e; font-weight: 600; }
.long { color: #3fb950; }
.short { color: #f85149; }
.win { color: #3fb950; font-weight: 700; }
.loss { color: #f85149; font-weight: 700; }
.empty { color: #8b949e; font-style: italic; padding: 12px 0; }
"""


def _rows(conn):
    conn.row_factory = sqlite3.Row
    open_t = conn.execute(
        "SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY score DESC, opened_at DESC"
    ).fetchall()
    closed_t = conn.execute(
        "SELECT * FROM paper_trades WHERE status IN ('WIN','LOSS','EXPIRED') ORDER BY closed_at DESC LIMIT 50"
    ).fetchall()
    wins = conn.execute("SELECT COUNT(*) FROM paper_trades WHERE status='WIN'").fetchone()[0]
    losses = conn.execute("SELECT COUNT(*) FROM paper_trades WHERE status='LOSS'").fetchone()[0]
    expired = conn.execute("SELECT COUNT(*) FROM paper_trades WHERE status='EXPIRED'").fetchone()[0]
    avg = conn.execute(
        "SELECT AVG(pnl_pct) FROM paper_trades WHERE status IN ('WIN','LOSS')"
    ).fetchone()[0]
    return open_t, closed_t, wins, losses, expired, avg


def _by_strategy(conn):
    """Per-strategy scoreboard rows: which strategy is actually winning."""
    strategies = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT strategy FROM paper_trades WHERE strategy IS NOT NULL"
        ).fetchall()
    ]
    rows = []
    for strat in strategies:
        wins = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE strategy=? AND status='WIN'", (strat,)
        ).fetchone()[0]
        losses = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE strategy=? AND status='LOSS'", (strat,)
        ).fetchone()[0]
        open_c = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE strategy=? AND status='OPEN'", (strat,)
        ).fetchone()[0]
        avg = conn.execute(
            "SELECT AVG(pnl_pct) FROM paper_trades WHERE strategy=? AND status IN ('WIN','LOSS')",
            (strat,),
        ).fetchone()[0]
        done = wins + losses
        rows.append({
            "strategy": strat,
            "open": open_c,
            "closed": done,
            "win_rate": round(wins / done * 100, 1) if done else 0.0,
            "avg_pnl": round(avg, 2) if avg is not None else 0.0,
        })
    rows.sort(key=lambda x: x["avg_pnl"], reverse=True)
    return rows


def _dir_span(d):
    cls = "long" if d == "LONG" else "short"
    return f'<span class="{cls}">{d}</span>'


def build():
    os.makedirs(OUT_DIR, exist_ok=True)

    conn = sqlite3.connect(DB)
    open_t, closed_t, wins, losses, expired, avg = _rows(conn)
    strat_rows = _by_strategy(conn)
    conn.close()

    health = health_monitor.coin_health()
    paused = sorted(c for c, h in health.items() if h["status"] == "PAUSED")

    closed = wins + losses
    win_rate = round(wins / closed * 100, 1) if closed else 0.0
    avg = round(avg, 2) if avg is not None else 0.0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Per-strategy scoreboard (which strategy is winning)
    if strat_rows:
        srows = "".join(
            f"<tr><td>{r['strategy']}</td><td>{r['open']}</td><td>{r['closed']}</td>"
            f"<td>{r['win_rate']}%</td>"
            f"<td class=\"{'win' if r['avg_pnl'] >= 0 else 'loss'}\">{r['avg_pnl']}%</td></tr>"
            for r in strat_rows
        )
        strat_table = (
            "<table><tr><th>Strategy</th><th>Open</th><th>Closed</th>"
            "<th>Win rate</th><th>Avg P&L</th></tr>"
            f"{srows}</table>"
        )
    else:
        strat_table = '<div class="empty">No strategy results yet.</div>'

    # Health monitor: paused coins (recent expectancy decayed) + trailing window
    if paused:
        hrows = "".join(
            f"<tr><td>{c}</td><td>{health[c]['trades']}</td>"
            f"<td>{health[c]['win_rate']}%</td>"
            f"<td class=\"loss\">{health[c]['avg_pnl']}%</td></tr>"
            for c in paused
        )
        health_table = (
            f"<div class='sub'>Coins paused for poor results over the last "
            f"{health_monitor.HEALTH_WINDOW_DAYS} days (auto-recover as trades age out).</div>"
            "<table><tr><th>Coin</th><th>Recent trades</th><th>Win rate</th>"
            f"<th>Avg P&L</th></tr>{hrows}</table>"
        )
    else:
        health_table = (
            '<div class="empty">All coins healthy — none paused '
            f"(watching the last {health_monitor.HEALTH_WINDOW_DAYS} days).</div>"
        )

    # Open-trades table
    if open_t:
        open_rows = "".join(
            f"<tr><td>{r['coin']}{' 🎯' if r['tp1_hit'] else ''}</td>"
            f"<td>{r['strategy'] or '-'}</td><td>{r['timeframe'] or '-'}</td>"
            f"<td>{_dir_span(r['direction'])}</td>"
            f"<td>{r['score']}%</td><td>{fmt_price(r['entry'])}</td>"
            f"<td>{fmt_price(r['stop'])}</td>"
            f"<td>{fmt_price(r['tp1'])}</td><td>{r['opened_at']}</td></tr>"
            for r in open_t
        )
        open_table = (
            "<table><tr><th>Coin</th><th>Strat</th><th>TF</th><th>Dir</th><th>Conf</th><th>Entry</th>"
            "<th>Stop</th><th>TP1</th><th>Opened (UTC)</th></tr>"
            f"{open_rows}</table>"
        )
    else:
        open_table = '<div class="empty">No open trades right now.</div>'

    # Closed-trades table
    if closed_t:
        closed_rows = ""
        for r in closed_t:
            cls = "win" if r["status"] == "WIN" else "loss" if r["status"] == "LOSS" else "empty"
            closed_rows += (
                f"<tr><td>{r['coin']}</td><td>{r['strategy'] or '-'}</td><td>{r['timeframe'] or '-'}</td>"
                f"<td>{_dir_span(r['direction'])}</td>"
                f'<td class="{cls}">{r["status"]}</td>'
                f'<td class="{cls}">{r["pnl_pct"]}%</td>'
                f"<td>{r['closed_at']}</td></tr>"
            )
        closed_table = (
            "<table><tr><th>Coin</th><th>Strat</th><th>TF</th><th>Dir</th><th>Result</th>"
            "<th>P&L</th><th>Closed (UTC)</th></tr>"
            f"{closed_rows}</table>"
        )
    else:
        closed_table = '<div class="empty">No closed trades yet - they resolve as price hits target or stop.</div>'

    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta http-equiv='refresh' content='120'>"
        "<title>Crypto Agent Dashboard</title>"
        f"<style>{CSS}</style></head><body>"
        "<h1>Crypto Agent - Paper Trading</h1>"
        f"<div class='sub'>Updated {now} - refreshes every 2 min</div>"
        "<div class='cards'>"
        f"<div class='card'><div class='label'>Win Rate</div><div class='value'>{win_rate}%</div></div>"
        f"<div class='card'><div class='label'>Open</div><div class='value'>{len(open_t)}</div></div>"
        f"<div class='card'><div class='label'>Wins</div><div class='value'>{wins}</div></div>"
        f"<div class='card'><div class='label'>Losses</div><div class='value'>{losses}</div></div>"
        f"<div class='card'><div class='label'>Expired</div><div class='value'>{expired}</div></div>"
        f"<div class='card'><div class='label'>Avg P&L</div><div class='value'>{avg}%</div></div>"
        "</div>"
        "<h2>Strategy scoreboard</h2>"
        f"{strat_table}"
        "<h2>Health monitor</h2>"
        f"{health_table}"
        "<h2>Open trades</h2>"
        f"{open_table}"
        "<h2>Recent results</h2>"
        f"{closed_table}"
        "</body></html>"
    )

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard written to {OUT_FILE} ({len(open_t)} open, {closed} closed)")


if __name__ == "__main__":
    build()
