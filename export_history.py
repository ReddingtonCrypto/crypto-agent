"""Export the paper-trade history to CSV so it survives cache eviction.

The live database exists only in the GitHub Actions cache, which GitHub can
evict at any time (7 days unused / storage pressure) — and with it the whole
forward-test record. Each scan run calls this and commits the CSV back to the
repo, so the history is permanent even if the cache (and open-trade state) is
lost.

Run: python export_history.py
"""

import csv
import os
import sqlite3

DB = "database/crypto.db"
OUT_DIR = "history"
OUT_FILE = os.path.join(OUT_DIR, "paper_trades.csv")


def export():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    # Deterministic order so an unchanged history produces an identical file
    # (the workflow only commits when the file actually changed).
    rows = conn.execute("SELECT * FROM paper_trades ORDER BY id").fetchall()
    conn.close()

    if not rows:
        print("No trades to export yet.")
        return

    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(rows[0].keys())
        writer.writerows([tuple(r) for r in rows])
    print(f"Exported {len(rows)} trades to {OUT_FILE}")


if __name__ == "__main__":
    export()
