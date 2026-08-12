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


_SCHEMA_READY = False


def _ensure_schema(conn):
    """Add the signal_ts column once (backwards-compatible migration). signal_ts
    identifies the CANDLE a setup triggered on, so the same setup can never open
    twice — killing the re-open/re-alert loop where a limit-entry trade opens,
    quick-closes, and the SAME bar re-triggers it on the next scan."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    cols = [r[1] for r in conn.execute("PRAGMA table_info(paper_trades)").fetchall()]
    if "signal_ts" not in cols:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN signal_ts INTEGER")
        conn.commit()
    if "alert_msg_id" not in cols:  # Telegram message_id of the setup alert, so
        # TP/loss replies can be threaded to it.
        conn.execute("ALTER TABLE paper_trades ADD COLUMN alert_msg_id INTEGER")
        conn.commit()
    _SCHEMA_READY = True


def set_alert_msg(trade_id, msg_id):
    """Store the Telegram message_id of the setup alert on the trade."""
    if msg_id is None:
        return
    conn = _conn()
    _ensure_schema(conn)
    conn.execute("UPDATE paper_trades SET alert_msg_id=? WHERE id=?", (int(msg_id), trade_id))
    conn.commit()
    conn.close()


def has_open_trade(coin, direction, timeframe, strategy):
    conn = _conn()
    row = conn.execute(
        "SELECT 1 FROM paper_trades WHERE coin=? AND direction=? AND timeframe=? "
        "AND strategy=? AND status='OPEN' LIMIT 1",
        (coin, direction, timeframe, strategy),
    ).fetchone()
    conn.close()
    return row is not None


def open_trade(coin, direction, entry, stop, tp1, tp2, score, timeframe, strategy,
               signal_ts=None):
    """Open a paper trade. Returns True if a new trade was opened.

    Blocked if EITHER a trade for this coin+direction+timeframe+strategy is
    already OPEN, OR (when signal_ts is given) a trade for this exact SIGNAL
    CANDLE already exists — open or closed. The second guard stops the re-open
    loop: a limit-entry setup that opens, quick-closes, then re-triggers on the
    very same candle on the next 5-min scan would otherwise re-open and re-alert
    over and over. One candle -> one trade -> one alert.
    """
    conn = _conn()
    _ensure_schema(conn)

    open_dupe = conn.execute(
        "SELECT 1 FROM paper_trades WHERE coin=? AND direction=? AND timeframe=? "
        "AND strategy=? AND status='OPEN' LIMIT 1",
        (coin, direction, timeframe, strategy),
    ).fetchone()
    if open_dupe:
        conn.close()
        return False

    if signal_ts is not None:
        same_bar = conn.execute(
            "SELECT 1 FROM paper_trades WHERE coin=? AND direction=? AND timeframe=? "
            "AND strategy=? AND signal_ts=? LIMIT 1",
            (coin, direction, timeframe, strategy, int(signal_ts)),
        ).fetchone()
        if same_bar:
            conn.close()
            return False

    conn.execute(
        """
        INSERT INTO paper_trades
        (coin, direction, entry, stop, tp1, tp2, score, timeframe, strategy, status,
         opened_at, signal_ts)
        VALUES (?,?,?,?,?,?,?,?,?, 'OPEN', ?, ?)
        """,
        (coin, direction, entry, stop, tp1, tp2, score, timeframe, strategy, _now(),
         int(signal_ts) if signal_ts is not None else None),
    )
    conn.commit()
    conn.close()
    return True


def create_pending(coin, direction, entry, stop, tp1, tp2, score, timeframe,
                   strategy, signal_ts=None):
    """Save a setup AWAITING human approval (status='PENDING'). It is NOT counted
    on the scoreboard until approved. Deduped on the signal candle so the same
    setup is only ever proposed once. Returns the new pending row id, or None if
    it already exists (any status)."""
    conn = _conn()
    _ensure_schema(conn)
    if signal_ts is not None:
        dupe = conn.execute(
            "SELECT id FROM paper_trades WHERE coin=? AND direction=? AND timeframe=? "
            "AND strategy=? AND signal_ts=? LIMIT 1",
            (coin, direction, timeframe, strategy, int(signal_ts)),
        ).fetchone()
        if dupe:
            conn.close()
            return None
    cur = conn.execute(
        """
        INSERT INTO paper_trades
        (coin, direction, entry, stop, tp1, tp2, score, timeframe, strategy, status,
         signal_ts)
        VALUES (?,?,?,?,?,?,?,?,?, 'PENDING', ?)
        """,
        (coin, direction, entry, stop, tp1, tp2, score, timeframe, strategy,
         int(signal_ts) if signal_ts is not None else None),
    )
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid


def get_pending(pending_id):
    """Return a PENDING trade as a dict, or None if not found / not pending."""
    conn = _conn()
    row = conn.execute(
        "SELECT id, coin, direction, entry, stop, tp1, tp2, timeframe, strategy, "
        "status, alert_msg_id FROM paper_trades WHERE id=?",
        (pending_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    keys = ["id", "coin", "direction", "entry", "stop", "tp1", "tp2",
            "timeframe", "strategy", "status", "alert_msg_id"]
    return dict(zip(keys, row))


def approve_pending(pending_id, current_price=None):
    """Human approved a PENDING setup. Decides MARKET vs LIMIT entry from the
    current price:
      * MARKET — price is already at/through the entry level in our favour
        (LONG: price <= entry; SHORT: price >= entry). Opens NOW, and the tracked
        entry becomes the ACTUAL current price at approval (what the dashboard
        shows).
      * LIMIT  — price hasn't reached the entry yet. Goes to WAITING; it only
        becomes a tracked OPEN trade once price touches the entry (handled in
        update_open_trades), filled at the entry level.
    Returns the trade dict (with extra keys mode='market'|'limit', fill_price) on
    success, or None if it wasn't pending / a live duplicate exists. Idempotent."""
    conn = _conn()
    row = conn.execute(
        "SELECT coin, direction, timeframe, strategy, status, entry, stop, tp1 "
        "FROM paper_trades WHERE id=?",
        (pending_id,),
    ).fetchone()
    if not row or row[4] != "PENDING":
        conn.close()
        return None
    coin, direction, timeframe, strategy, _, entry, stop, tp1 = row
    dupe = conn.execute(
        "SELECT 1 FROM paper_trades WHERE coin=? AND direction=? AND timeframe=? "
        "AND strategy=? AND status IN ('OPEN','WAITING') LIMIT 1",
        (coin, direction, timeframe, strategy),
    ).fetchone()
    if dupe:
        conn.execute("UPDATE paper_trades SET status='SKIPPED' WHERE id=?", (pending_id,))
        conn.commit()
        conn.close()
        return None

    # UNTOUCHED guard — the fix for "opens then instantly says TP hit". If price
    # has already reached the 50% (the move already delivered) or blown the stop
    # (invalidated) since the setup formed, there is no room left: do NOT open.
    if current_price is not None and tp1 is not None and stop is not None:
        if direction == "SHORT":
            delivered, invalid = current_price <= tp1, current_price >= stop
        else:
            delivered, invalid = current_price >= tp1, current_price <= stop
        if delivered or invalid:
            conn.execute("UPDATE paper_trades SET status='CANCELLED', closed_at=? WHERE id=?",
                         (_now(), pending_id))
            conn.commit()
            conn.close()
            return {"coin": coin, "direction": direction, "timeframe": timeframe,
                    "mode": "expired",
                    "reason": "already delivered to 50%" if delivered else "stop invalidated"}

    mode = "market"
    fill_price = entry
    if current_price is not None and entry:
        at_or_better = (current_price <= entry) if direction == "LONG" else (current_price >= entry)
        if at_or_better:
            mode, fill_price = "market", float(current_price)
        else:
            mode = "limit"

    if mode == "market":
        conn.execute(
            "UPDATE paper_trades SET status='OPEN', entry=?, opened_at=? WHERE id=?",
            (fill_price, _now(), pending_id),
        )
    else:
        conn.execute(
            "UPDATE paper_trades SET status='WAITING', opened_at=? WHERE id=?",
            (_now(), pending_id),
        )
    conn.commit()
    conn.close()
    d = get_pending(pending_id)
    if d:
        d["mode"] = mode
        d["fill_price"] = fill_price if mode == "market" else entry
    return d


def pending_progress(row, price):
    """How far a pending setup has already travelled, as a fraction of the WHOLE
    trade — entry all the way to the final target. Returns (fraction, invalid).

    Measured against TP2 rather than TP1 because the full move is what you are
    being offered; a third of the total gone is a third of the reward gone.
    """
    _tid, _coin, direction, entry, stop, _tp1, tp2 = row[:7]
    if not entry or tp2 is None or price is None:
        return None, False
    long_ = direction == "LONG"
    invalid = (price <= stop) if long_ else (price >= stop)
    span = abs(tp2 - entry)
    if span <= 0:
        return None, invalid
    moved = (price - entry) if long_ else (entry - price)
    return moved / span, invalid


def expire_stale_pending(bars, frac=0.30):
    """Retire PENDING setups whose move has already left without you.

    A setup you haven't tapped yet is only worth taking while the trade is still
    ahead of it. Once price has covered `frac` of the WHOLE move — entry to the
    final target, both take-profits included — that share of the reward is gone
    while the stop has not moved, so the setup is cancelled rather than left
    sitting there going stale.

    Also drops any setup already invalidated (price through the stop). Returns a
    list of {coin, timeframe, strategy, reason} for logging.
    `bars` is {coin: {high, low, price}}.
    """
    conn = _conn()
    rows = conn.execute(
        "SELECT id, coin, direction, entry, stop, tp1, tp2, timeframe, strategy "
        "FROM paper_trades WHERE status='PENDING'"
    ).fetchall()
    gone = []
    for r in rows:
        tid, coin, direction, entry, stop, tp1, tp2, timeframe, strategy = r
        bar = bars.get(coin)
        if bar is None:
            continue
        moved, invalid = pending_progress(r, bar.get("price"))
        reason = None
        if invalid:
            reason = "invalidated"
        elif moved is not None and moved >= frac:
            reason = f"already ran {moved * 100:.0f}% of the move"
        if reason:
            conn.execute(
                "UPDATE paper_trades SET status='CANCELLED', closed_at=? WHERE id=?",
                (_now(), tid),
            )
            gone.append({"coin": coin, "timeframe": timeframe,
                         "strategy": strategy, "reason": reason})
    if gone:
        conn.commit()
    conn.close()
    return gone


def count_pending():
    """How many setups are sitting waiting for a tap right now."""
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status='PENDING'").fetchone()[0]
    conn.close()
    return n


def reject_pending(pending_id):
    """Human skipped a PENDING setup -> mark SKIPPED (never tracked). Returns True
    if a pending row was updated."""
    conn = _conn()
    cur = conn.execute(
        "UPDATE paper_trades SET status='SKIPPED' WHERE id=? AND status='PENDING'",
        (pending_id,),
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n > 0


# --------------------------------------------------------------------------- #
#  TREND positions (BTC-regime-gated trend-following) — a persistent LONG/FLAT
#  position that exits on a SIGNAL flip (trend or BTC regime turns off), NOT on a
#  fixed TP/SL. Tracked separately so update_open_trades leaves them alone.
# --------------------------------------------------------------------------- #
def open_trend(coin, entry, timeframe, strategy="TrendGated", signal_ts=None):
    """Open a trend LONG (once per coin+strategy). Returns True if opened."""
    conn = _conn()
    _ensure_schema(conn)
    dupe = conn.execute(
        "SELECT 1 FROM paper_trades WHERE coin=? AND strategy=? AND status='OPEN' LIMIT 1",
        (coin, strategy),
    ).fetchone()
    if dupe:
        conn.close()
        return False
    conn.execute(
        "INSERT INTO paper_trades (coin, direction, entry, timeframe, strategy, "
        "status, opened_at, signal_ts) VALUES (?, 'LONG', ?, ?, ?, 'OPEN', ?, ?)",
        (coin, entry, timeframe, strategy, _now(),
         int(signal_ts) if signal_ts is not None else None),
    )
    conn.commit()
    conn.close()
    return True


def get_open_trends(strategy="TrendGated"):
    conn = _conn()
    rows = conn.execute(
        "SELECT id, coin, entry, timeframe FROM paper_trades "
        "WHERE strategy=? AND status='OPEN'", (strategy,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "coin": r[1], "entry": r[2], "timeframe": r[3]} for r in rows]


def close_trend(trade_id, exit_price):
    """Close a trend LONG at exit_price (signal flip). Returns {result, pnl_pct}."""
    conn = _conn()
    row = conn.execute("SELECT entry FROM paper_trades WHERE id=? AND status='OPEN'",
                       (trade_id,)).fetchone()
    if not row or not row[0]:
        conn.close()
        return None
    entry = row[0]
    pnl = round((exit_price - entry) / entry * 100.0, 2)
    result = "WIN" if pnl > 0 else "LOSS"
    conn.execute(
        "UPDATE paper_trades SET status=?, closed_at=?, exit_price=?, pnl_pct=? WHERE id=?",
        (result, _now(), exit_price, pnl, trade_id),
    )
    conn.commit()
    conn.close()
    return {"result": result, "pnl_pct": pnl}


PARTIAL_FRAC = 0.5   # fraction of the position banked at TP1 (rest runs to TP2)


def _leg(entry, exit_price, direction):
    """Signed % return of one exit level, favourable-positive for the trade."""
    r = (exit_price - entry) / entry * 100.0
    return -r if direction == "SHORT" else r


def update_open_trades(bars):
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
        "opened_at, tp1_hit, realized_pct, alert_msg_id FROM paper_trades WHERE status='OPEN' "
        "AND (strategy IS NULL OR strategy != 'TrendGated')"
    ).fetchall()

    events = []
    for (tid, coin, direction, entry, stop, tp1, tp2, timeframe, strategy,
         opened_at, tp1_hit, realized_pct, alert_msg_id) in rows:
        bar = bars.get(coin)
        if bar is None or not entry:  # skip missing data or bad (zero) entry
            continue

        # Intraday high/low so we don't miss a level hit then retraced.
        hi, lo = bar["high"], bar["low"]
        realized_pct = realized_pct or 0.0

        # CRT now uses the same partial-exit plan as ICT (bank 50% at TP1 = 2R,
        # move the runner's stop to break-even, runner to the C1-body target) —
        # the group's exact risk management — so it flows through the path below.

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
                    "alert_msg_id": alert_msg_id,
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
                    "alert_msg_id": alert_msg_id,
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
                "alert_msg_id": alert_msg_id,
            })

    # ---- Fills: promote WAITING (limit) trades to OPEN when price arrives, or
    #      cancel them if they wait past the time-stop without filling. ----
    waiting = conn.execute(
        "SELECT id, coin, direction, entry, timeframe, opened_at, strategy, alert_msg_id "
        "FROM paper_trades WHERE status='WAITING'"
    ).fetchall()
    for (tid, coin, direction, entry, timeframe, opened_at, strategy, alert_msg_id) in waiting:
        bar = bars.get(coin)
        if bar is None or not entry:
            continue
        filled = (bar["low"] <= entry) if direction == "LONG" else (bar["high"] >= entry)
        if filled:
            conn.execute(
                "UPDATE paper_trades SET status='OPEN', opened_at=? WHERE id=?",
                (_now(), tid),
            )
            events.append({"coin": coin, "direction": direction, "result": "FILLED",
                           "pnl_pct": 0.0, "timeframe": timeframe, "strategy": strategy,
                           "alert_msg_id": alert_msg_id})
        elif _expired(opened_at, timeframe):
            conn.execute(
                "UPDATE paper_trades SET status='CANCELLED', closed_at=? WHERE id=?",
                (_now(), tid),
            )
            events.append({"coin": coin, "direction": direction, "result": "CANCELLED",
                           "pnl_pct": 0.0, "timeframe": timeframe, "strategy": strategy,
                           "alert_msg_id": alert_msg_id})

    conn.commit()
    conn.close()
    return events  # partial-bank + full-close + fill/cancel events


def get_stats():
    conn = _conn()
    open_count = conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status='OPEN'"
    ).fetchone()[0]
    waiting = conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status='WAITING'"
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
        "waiting": waiting,
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
