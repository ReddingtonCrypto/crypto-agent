"""Retro-apply the odd-price SL/TP placement to CRT trades that are already live.

Trades opened before the change still carry the raw levels: the stop sitting
exactly on the sweep wick, the targets sitting exactly on the opposing pool.
This walks the OPEN/WAITING CRT trades and moves them onto the same odd,
non-round prices the detector now produces:

  * stop  -> slightly BEYOND the wick (further from entry, never closer)
  * TP1/2 -> slightly INSIDE the level (closer to entry, never greedier)

Break-even runners (tp1_hit=1, stop deliberately parked at entry) keep their
stop; only their remaining target is adjusted.

These nudges are NOT idempotent — re-running against already-adjusted prices
would shift them a second time. So the levels are always recomputed from a
SOURCE database holding the original raw values (default: the live DB, which is
only correct on the first run). Pass --source to recompute from a pre-change
backup instead; the result is then the same no matter how often it is run.

Usage:  python retro_sl_tp.py                        # dry run, prints the diff
        python retro_sl_tp.py --source DB            # raw levels from a backup
        python retro_sl_tp.py --source DB --apply    # writes it
"""

import sqlite3
import sys

from strategies.smc.crt import (_entry_easier_fill, _sl_beyond_wick,
                                _tp_inside_target)

DB = "database/crypto.db"

QUERY = ("SELECT id, coin, timeframe, direction, status, entry, stop, tp1, tp2, tp1_hit "
         "FROM paper_trades WHERE strategy='CRT' AND status IN ('OPEN','WAITING') "
         "ORDER BY timeframe, coin")


def main(apply_changes, source=None):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    if source:
        # Raw, un-nudged levels come from the backup; ids match the live DB.
        src = sqlite3.connect(source)
        src.row_factory = sqlite3.Row
        rows = src.execute(QUERY).fetchall()
        live = {r["id"]: r for r in conn.execute(QUERY).fetchall()}
        rows = [r for r in rows if r["id"] in live]
        print(f"(recomputing from raw levels in {source})\n")
    else:
        rows = conn.execute(QUERY).fetchall()

    print(f"{'coin':12} {'tf':3} {'dir':5} {'field':4} {'old':>12} -> {'new':>12}  {'shift':>8}")
    print("-" * 70)
    updates = []
    for r in rows:
        d, entry = r["direction"], r["entry"]
        new_stop, new_tp1, new_tp2 = r["stop"], r["tp1"], r["tp2"]

        # Only an UNFILLED limit may have its entry moved. An OPEN trade's entry
        # is a price we actually got - rewriting it would falsify the record.
        if r["status"] == "WAITING" and entry:
            entry = _entry_easier_fill(r["entry"], d)
        # Break-even runners keep their stop at entry - that protection is deliberate.
        if not r["tp1_hit"] and r["stop"]:
            new_stop = _sl_beyond_wick(r["stop"], d)
        if r["tp1"]:
            new_tp1 = _tp_inside_target(r["tp1"], d)
        if r["tp2"]:
            new_tp2 = _tp_inside_target(r["tp2"], d)

        for field, old, new in (("entry", r["entry"], entry),
                                ("stop", r["stop"], new_stop),
                                ("tp1", r["tp1"], new_tp1),
                                ("tp2", r["tp2"], new_tp2)):
            if old and new and abs(new - old) > 1e-12:
                pct = (new - old) / old * 100
                note = " (BE - kept)" if field == "stop" and r["tp1_hit"] else ""
                print(f"{r['coin']:12} {r['timeframe']:3} {d:5} {field:4} "
                      f"{old:>12.8g} -> {new:>12.8g}  {pct:>+7.3f}%{note}")

        # Safety: the geometry must still make sense after the move.
        ordered = (new_stop < entry < new_tp1 <= new_tp2) if d == "LONG" \
            else (new_stop > entry > new_tp1 >= new_tp2)
        if not (ordered or r["tp1_hit"]):
            print(f"  !! SKIPPED {r['coin']} {r['timeframe']} - geometry would break")
            continue
        updates.append((entry, new_stop, new_tp1, new_tp2, r["id"]))

    print("-" * 70)
    if not apply_changes:
        print(f"DRY RUN - {len(updates)} trade(s) would be updated. Re-run with --apply.")
        return

    conn.executemany(
        "UPDATE paper_trades SET entry=?, stop=?, tp1=?, tp2=? WHERE id=?", updates
    )
    conn.commit()
    print(f"APPLIED to {len(updates)} trade(s).")


if __name__ == "__main__":
    src = None
    if "--source" in sys.argv:
        src = sys.argv[sys.argv.index("--source") + 1]
    main("--apply" in sys.argv, src)
