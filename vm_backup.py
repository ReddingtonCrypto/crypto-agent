"""VM-side backup: a consistent sqlite snapshot of the trade database plus a
human-readable CSV, with old snapshots auto-pruned. Backups are tiny (KB each),
so retention is cheap.

Run on a schedule (systemd timer). Snapshots land in history/backups/.
"""
import glob
import os
import sqlite3
import time
from datetime import datetime, timezone

import export_history

DB = "database/crypto.db"
BK = "history/backups"
KEEP_DAYS = 30


def main():
    os.makedirs(BK, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = os.path.join(BK, f"crypto_{date}.db")

    # Consistent snapshot via sqlite's backup API (safe while the bot writes).
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()

    # Refresh the human-readable CSV too.
    export_history.export()

    # Prune snapshots older than KEEP_DAYS.
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in glob.glob(os.path.join(BK, "crypto_*.db")):
        if os.path.getmtime(f) < cutoff:
            os.remove(f)

    kept = len(glob.glob(os.path.join(BK, "crypto_*.db")))
    print(f"Backup written: {dest} ({kept} snapshots kept)")


if __name__ == "__main__":
    main()
