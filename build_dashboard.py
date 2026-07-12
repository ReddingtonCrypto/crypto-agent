"""Generate a single static dashboard page (site/index.html) from the database.

Shows the accuracy scoreboard, open paper trades, and recent results.
Published free via GitHub Pages, viewable on your phone.
"""

import os
import sqlite3
from datetime import datetime, timezone

from formatting import fmt_price
import health_monitor
import sector_flow


DB = "database/crypto.db"
OUT_DIR = "site"
OUT_FILE = os.path.join(OUT_DIR, "index.html")


CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px; max-width: 900px; margin: 0 auto;
  background: #0d1117; color: #e6edf3;
  font-family: -apple-system, Segoe UI, Roboto, sans-serif;
}
h1 { font-size: 21px; margin: 0 0 4px; }
.sub { color: #8b949e; font-size: 12px; margin: 2px 0 4px; }
.sub b { color: #c9d1d9; }

/* Panels group related content into clear cards */
.panel {
  background: #11161d; border: 1px solid #30363d; border-radius: 12px;
  padding: 14px 16px; margin: 14px 0;
}
.panel.accent { border-left: 4px solid #3fb950; }
.panel > h2:first-child { margin-top: 0; }
h2 { font-size: 15px; margin: 0 0 10px; }
h2 .tag { font-size: 11px; color: #8b949e; font-weight: 500; }

.cards { display: flex; flex-wrap: wrap; gap: 10px; }
.card {
  background: #161b22; border: 1px solid #30363d; border-radius: 10px;
  padding: 10px 14px; min-width: 88px; flex: 1;
}
.card .label { color: #8b949e; font-size: 11px; text-transform: uppercase; }
.card .value { font-size: 22px; font-weight: 700; margin-top: 4px; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 7px 6px; border-bottom: 1px solid #21262d; }
th { color: #8b949e; font-weight: 600; }
tr:nth-child(even) td { background: #0e131a; }
.long { color: #3fb950; font-weight: 600; }
.short { color: #f85149; font-weight: 600; }
.win { color: #3fb950; font-weight: 700; }
.loss { color: #f85149; font-weight: 700; }
.empty { color: #8b949e; font-style: italic; padding: 10px 0; }
.pill { display:inline-block; padding:1px 7px; border-radius:20px; font-size:11px; font-weight:700; }
.pill.ok { background:#132b1a; color:#3fb950; }
.pill.bad { background:#2b1414; color:#f85149; }
.status { padding:11px 14px; border-radius:10px; font-size:13px; font-weight:600; margin:12px 0; }
.status.ok { background:#0f2417; color:#3fb950; border:1px solid #1f6f3d; }
.status.bad { background:#2b1414; color:#f85149; border:1px solid #6f1f1f; }
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


def _equity_curve(conn, direction=None):
    """Cumulative realized P&L (%) over closed trades, oldest first.
    Pass direction='LONG' to chart only the trades you actually take."""
    q = ("SELECT pnl_pct FROM paper_trades "
         "WHERE status IN ('WIN','LOSS','EXPIRED') AND pnl_pct IS NOT NULL ")
    args = ()
    if direction:
        q += "AND direction=? "
        args = (direction,)
    q += "ORDER BY closed_at ASC"
    rows = conn.execute(q, args).fetchall()
    curve, total = [0.0], 0.0
    for r in rows:
        total += r[0]
        curve.append(round(total, 2))
    return curve


def _by_direction(conn):
    """Stats split by LONG vs SHORT — you only trade LONG (spot buys), so this
    lets you read your real performance apart from the shorts."""
    out = {}
    for d in ("LONG", "SHORT"):
        wins = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE direction=? AND status='WIN'", (d,)
        ).fetchone()[0]
        losses = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE direction=? AND status='LOSS'", (d,)
        ).fetchone()[0]
        open_c = conn.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE direction=? AND status='OPEN'", (d,)
        ).fetchone()[0]
        avg = conn.execute(
            "SELECT AVG(pnl_pct) FROM paper_trades WHERE direction=? AND status IN ('WIN','LOSS')",
            (d,),
        ).fetchone()[0]
        total = conn.execute(
            "SELECT SUM(pnl_pct) FROM paper_trades "
            "WHERE direction=? AND status IN ('WIN','LOSS','EXPIRED') AND pnl_pct IS NOT NULL",
            (d,),
        ).fetchone()[0]
        closed = wins + losses
        out[d] = {
            "open": open_c,
            "closed": closed,
            "wins": wins,
            "win_rate": round(wins / closed * 100, 1) if closed else 0.0,
            "avg_pnl": round(avg, 2) if avg is not None else 0.0,
            "total_pnl": round(total, 1) if total is not None else 0.0,
        }
    return out


def _equity_svg(curve, width=700, height=180):
    """Render the cumulative P&L curve as a small inline SVG (no JS needed)."""
    if len(curve) < 2:
        return '<div class="empty">Not enough closed trades for a curve yet.</div>'
    lo, hi = min(curve), max(curve)
    span = (hi - lo) or 1.0
    pad = 6
    n = len(curve) - 1
    pts = " ".join(
        f"{pad + i / n * (width - 2 * pad):.1f},"
        f"{pad + (hi - v) / span * (height - 2 * pad):.1f}"
        for i, v in enumerate(curve)
    )
    # Zero line, if 0 falls inside the plotted range.
    zero = ""
    if lo <= 0 <= hi:
        zy = pad + hi / span * (height - 2 * pad)
        zero = (f'<line x1="{pad}" y1="{zy:.1f}" x2="{width - pad}" y2="{zy:.1f}" '
                'stroke="#30363d" stroke-dasharray="4 4"/>')
    color = "#3fb950" if curve[-1] >= 0 else "#f85149"
    return (
        f'<div class="sub">Cumulative realized P&L over all {n} closed trades '
        f'(incl. expired): <b style="color:{color}">{curve[-1]:+.2f}%</b></div>'
        f'<svg viewBox="0 0 {width} {height}" '
        'style="width:100%;height:auto;background:#161b22;'
        'border:1px solid #30363d;border-radius:10px">'
        f"{zero}"
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>'
        "</svg>"
    )


def _dir_span(d):
    cls = "long" if d == "LONG" else "short"
    return f'<span class="{cls}">{d}</span>'


def _system_status_html():
    """One clear line: green if the bot is running smoothly, red only if the
    last scan failed and needs manual attention (you're also pinged on Telegram)."""
    import run_health
    rh = run_health.load()
    if not rh or not rh.get("last"):
        return "<div class='status ok'>⏳ Starting up — waiting for the first scan…</div>"
    last = rh["last"]
    if last["status"] == "ok":
        return (
            "<div class='status ok'>✅ Running smoothly — everything is working. "
            f"Last scan {last['time']} (scans every 5 min).</div>"
        )
    return (
        "<div class='status bad'>⚠️ PROBLEM — the last scan failed and needs your attention "
        f"(you've also been alerted on Telegram).<br>{last['time']} — {last.get('error', '')}</div>"
    )


def build():
    os.makedirs(OUT_DIR, exist_ok=True)

    conn = sqlite3.connect(DB)
    open_t, closed_t, wins, losses, expired, avg = _rows(conn)
    strat_rows = _by_strategy(conn)
    curve = _equity_curve(conn)
    long_curve = _equity_curve(conn, "LONG")
    short_curve = _equity_curve(conn, "SHORT")
    by_dir = _by_direction(conn)
    conn.close()

    health = health_monitor.coin_health()
    paused = sorted(c for c, h in health.items() if h["status"] == "PAUSED")

    # Narrative / sector heat (free; may be empty if tickers unavailable).
    # Creating the exchange also tells us which data source is live (badge).
    import data_source
    try:
        heat = sector_flow.sector_heat(data_source.make_exchange())
    except Exception:
        heat = []
    source = data_source.SOURCE_LABEL

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

    # Narrative / sector heat table (hottest first)
    if heat:
        hrows = "".join(
            f"<tr><td>{h['sector']}</td><td>{h['coins']}</td>"
            f"<td class=\"{'win' if h['avg_pct'] >= 0 else 'loss'}\">{h['avg_pct']:+.2f}%</td></tr>"
            for h in heat
        )
        sector_table = (
            "<div class='sub'>Which narratives money is rotating into (24h avg).</div>"
            "<table><tr><th>Sector</th><th>Coins</th><th>24h avg</th></tr>"
            f"{hrows}</table>"
        )
    else:
        sector_table = '<div class="empty">Sector heat unavailable right now.</div>'

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

    # Open-trades table, all directions (longs listed first).
    if open_t:
        ordered = sorted(open_t, key=lambda r: 0 if r["direction"] == "LONG" else 1)
        open_rows = "".join(
            f"<tr><td>{r['coin']}{' 🎯' if r['tp1_hit'] else ''}</td>"
            f"<td>{_dir_span(r['direction'])}</td>"
            f"<td>{r['strategy'] or '-'}</td><td>{r['timeframe'] or '-'}</td>"
            f"<td>{r['score']}%</td><td>{fmt_price(r['entry'])}</td>"
            f"<td>{fmt_price(r['stop'])}</td>"
            f"<td>{fmt_price(r['tp1'])}</td><td>{r['opened_at']}</td></tr>"
            for r in ordered
        )
        open_table = (
            "<table><tr><th>Coin</th><th>Dir</th><th>Strat</th><th>TF</th><th>Conf</th>"
            "<th>Entry</th><th>Stop</th><th>TP1</th><th>Opened (UTC)</th></tr>"
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

    # Direction P&L panel (reused for LONG and SHORT).
    def _dir_panel(title, emoji, stats, curve_svg, accent=False):
        avg_cls = "win" if stats["avg_pnl"] >= 0 else "loss"
        tot_cls = "win" if stats["total_pnl"] >= 0 else "loss"
        acls = " accent" if accent else ""
        return (
            f"<div class='panel{acls}'>"
            f"<h2>{emoji} {title}</h2>"
            "<div class='cards'>"
            f"<div class='card'><div class='label'>Win Rate</div><div class='value'>{stats['win_rate']}%</div></div>"
            f"<div class='card'><div class='label'>Open</div><div class='value'>{stats['open']}</div></div>"
            f"<div class='card'><div class='label'>Closed</div><div class='value'>{stats['closed']}</div></div>"
            f"<div class='card'><div class='label'>Avg P&L</div><div class='value {avg_cls}'>{stats['avg_pnl']:+}%</div></div>"
            f"<div class='card'><div class='label'>Total P&L</div><div class='value {tot_cls}'>{stats['total_pnl']:+}%</div></div>"
            "</div>"
            f"<div style='margin-top:12px'>{curve_svg}</div>"
            "</div>"
        )

    long_panel = _dir_panel("Longs P&L", "📈", by_dir["LONG"], _equity_svg(long_curve), accent=True)
    short_panel = _dir_panel("Shorts P&L", "📉", by_dir["SHORT"], _equity_svg(short_curve))

    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta http-equiv='refresh' content='120'>"
        "<title>Crypto Agent Dashboard</title>"
        f"<style>{CSS}</style></head><body>"
        "<h1>Crypto Agent — Paper Trading</h1>"
        f"<div class='sub'>Updated {now} · data: <b>{source}</b></div>"

        # ---- System status (green = smooth, red = needs you) ----
        f"{_system_status_html()}"

        # ---- Narrative / sector heat ----
        "<div class='panel'>"
        "<h2>Narrative / sector heat</h2>"
        f"{sector_table}"
        "</div>"

        # ---- Open trades (all directions) ----
        "<div class='panel'>"
        f"<h2>Open trades ({len(open_t)})</h2>"
        f"{open_table}"
        "</div>"

        # ---- Strategy scoreboard (with P&L) ----
        "<div class='panel'>"
        "<h2>Strategies</h2>"
        f"{strat_table}"
        "</div>"

        # ---- Longs then Shorts P&L ----
        f"{long_panel}"
        f"{short_panel}"

        # ---- Coin health monitor ----
        "<div class='panel'>"
        "<h2>Coin health monitor</h2>"
        f"{health_table}"
        "</div>"

        # ---- Trade history (at the end) ----
        "<div class='panel'>"
        "<h2>Trade history</h2>"
        f"{closed_table}"
        "</div>"
        "</body></html>"
    )

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard written to {OUT_FILE} ({len(open_t)} open, {closed} closed)")


if __name__ == "__main__":
    build()
