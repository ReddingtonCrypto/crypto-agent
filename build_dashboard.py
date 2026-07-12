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


def _run_health_html():
    """Show whether recent scan cycles succeeded — so a broken bot is obvious
    instead of just a silently stale dashboard."""
    import run_health
    rh = run_health.load()
    if not rh or not rh.get("last"):
        return '<div class="empty">No scan recorded yet (starts after the first run).</div>'

    last = rh["last"]
    ok = last["status"] == "ok"
    cls = "win" if ok else "loss"
    label = "OK" if ok else "ERROR"
    extra = f" — {last['error']}" if (not ok and last.get("error")) else ""

    rows = ""
    for e in reversed(rh.get("history", [])[-10:]):
        c = "win" if e["status"] == "ok" else "loss"
        if e["status"] == "ok":
            info = f"open {e.get('open', '-')}, closed {e.get('closed', '-')}, win {e.get('win_rate', '-')}%"
        else:
            info = e.get("error", "")
        rows += (
            f"<tr><td>{e['time']}</td>"
            f"<td class='{c}'>{e['status'].upper()}</td><td>{info}</td></tr>"
        )

    return (
        f"<div class='sub'>Last scan: {last['time']} — <b class='{cls}'>{label}</b>{extra} "
        "(scans every 5 min)</div>"
        "<table><tr><th>Time (UTC)</th><th>Status</th><th>Details</th></tr>"
        f"{rows}</table>"
    )


def build():
    os.makedirs(OUT_DIR, exist_ok=True)

    conn = sqlite3.connect(DB)
    open_t, closed_t, wins, losses, expired, avg = _rows(conn)
    strat_rows = _by_strategy(conn)
    curve = _equity_curve(conn)
    long_curve = _equity_curve(conn, "LONG")
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

    # Open-trades tables, split by side (longs are what you actually trade).
    def _open_table(rows):
        if not rows:
            return '<div class="empty">None open right now.</div>'
        body = "".join(
            f"<tr><td>{r['coin']}{' 🎯' if r['tp1_hit'] else ''}</td>"
            f"<td>{r['strategy'] or '-'}</td><td>{r['timeframe'] or '-'}</td>"
            f"<td>{r['score']}%</td><td>{fmt_price(r['entry'])}</td>"
            f"<td>{fmt_price(r['stop'])}</td>"
            f"<td>{fmt_price(r['tp1'])}</td><td>{r['opened_at']}</td></tr>"
            for r in rows
        )
        return (
            "<table><tr><th>Coin</th><th>Strat</th><th>TF</th><th>Conf</th><th>Entry</th>"
            "<th>Stop</th><th>TP1</th><th>Opened (UTC)</th></tr>"
            f"{body}</table>"
        )
    open_long = [r for r in open_t if r["direction"] == "LONG"]
    open_short = [r for r in open_t if r["direction"] == "SHORT"]
    open_long_table = _open_table(open_long)
    open_short_table = _open_table(open_short)

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

    # LONG panel cards (what you actually trade)
    lg = by_dir["LONG"]
    lg_avg_cls = "win" if lg["avg_pnl"] >= 0 else "loss"
    lg_tot_cls = "win" if lg["total_pnl"] >= 0 else "loss"

    # Direction-breakdown table (LONG vs SHORT)
    def _dir_row(d):
        s = by_dir[d]
        acls = "win" if s["avg_pnl"] >= 0 else "loss"
        tcls = "win" if s["total_pnl"] >= 0 else "loss"
        return (
            f"<tr><td>{_dir_span(d)}</td><td>{s['open']}</td><td>{s['closed']}</td>"
            f"<td>{s['win_rate']}%</td>"
            f"<td class='{acls}'>{s['avg_pnl']:+}%</td>"
            f"<td class='{tcls}'>{s['total_pnl']:+}%</td></tr>"
        )
    dir_table = (
        "<table><tr><th>Direction</th><th>Open</th><th>Closed</th><th>Win rate</th>"
        "<th>Avg P&L</th><th>Total P&L</th></tr>"
        f"{_dir_row('LONG')}{_dir_row('SHORT')}</table>"
    )

    # Run-health one-liner for the header
    import run_health
    rh = run_health.load()
    if rh and rh.get("last"):
        _ok = rh["last"]["status"] == "ok"
        health_pill = f"<span class='pill {'ok' if _ok else 'bad'}'>{'OK' if _ok else 'ERROR'}</span>"
        health_hdr = f" · last scan {rh['last']['time'][11:16]} UTC {health_pill}"
    else:
        health_hdr = ""

    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta http-equiv='refresh' content='120'>"
        "<title>Crypto Agent Dashboard</title>"
        f"<style>{CSS}</style></head><body>"
        "<h1>Crypto Agent — Paper Trading</h1>"
        f"<div class='sub'>Updated {now} · data: <b>{source}</b>{health_hdr}</div>"

        # ---- LONG panel: the trades you actually take ----
        "<div class='panel accent'>"
        "<h2>📈 Longs only <span class='tag'>— what you actually trade (spot buys)</span></h2>"
        "<div class='cards'>"
        f"<div class='card'><div class='label'>Win Rate</div><div class='value'>{lg['win_rate']}%</div></div>"
        f"<div class='card'><div class='label'>Open</div><div class='value'>{lg['open']}</div></div>"
        f"<div class='card'><div class='label'>Closed</div><div class='value'>{lg['closed']}</div></div>"
        f"<div class='card'><div class='label'>Avg P&L</div><div class='value {lg_avg_cls}'>{lg['avg_pnl']:+}%</div></div>"
        f"<div class='card'><div class='label'>Total P&L</div><div class='value {lg_tot_cls}'>{lg['total_pnl']:+}%</div></div>"
        "</div>"
        f"<div style='margin-top:12px'>{_equity_svg(long_curve)}</div>"
        "</div>"

        # ---- All-directions reference ----
        "<div class='panel'>"
        "<h2>All directions <span class='tag'>— reference (includes shorts the bot won't act on)</span></h2>"
        "<div class='cards'>"
        f"<div class='card'><div class='label'>Win Rate</div><div class='value'>{win_rate}%</div></div>"
        f"<div class='card'><div class='label'>Open</div><div class='value'>{len(open_t)}</div></div>"
        f"<div class='card'><div class='label'>Wins</div><div class='value'>{wins}</div></div>"
        f"<div class='card'><div class='label'>Losses</div><div class='value'>{losses}</div></div>"
        f"<div class='card'><div class='label'>Expired</div><div class='value'>{expired}</div></div>"
        f"<div class='card'><div class='label'>Avg P&L</div><div class='value'>{avg}%</div></div>"
        "</div>"
        f"<div style='margin-top:12px'>{dir_table}</div>"
        f"<div style='margin-top:12px'>{_equity_svg(curve)}</div>"
        "</div>"

        # ---- Open trades, split by side ----
        "<div class='panel'>"
        f"<h2>Open LONG trades <span class='tag'>({len(open_long)})</span></h2>"
        f"{open_long_table}"
        f"<h2 style='margin-top:16px'>Open SHORT trades <span class='tag'>({len(open_short)}) — not acted on</span></h2>"
        f"{open_short_table}"
        "</div>"

        # ---- Recent results ----
        "<div class='panel'>"
        "<h2>Recent results</h2>"
        f"{closed_table}"
        "</div>"

        # ---- Diagnostics: run health, strategy, sector, coin health ----
        "<div class='panel'>"
        "<h2>Run health <span class='tag'>— did each scan succeed</span></h2>"
        f"{_run_health_html()}"
        "</div>"
        "<div class='panel'>"
        "<h2>Strategy scoreboard</h2>"
        f"{strat_table}"
        "<h2 style='margin-top:16px'>Narrative / sector heat</h2>"
        f"{sector_table}"
        "<h2 style='margin-top:16px'>Coin health monitor</h2>"
        f"{health_table}"
        "</div>"
        "</body></html>"
    )

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard written to {OUT_FILE} ({len(open_t)} open, {closed} closed)")


if __name__ == "__main__":
    build()
