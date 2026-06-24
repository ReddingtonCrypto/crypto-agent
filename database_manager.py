import sqlite3
import os


os.makedirs("database", exist_ok=True)


connection = sqlite3.connect(
    "database/crypto.db"
)

cursor = connection.cursor()


# History of distinct signals (one row per new setup).
cursor.execute("""
CREATE TABLE IF NOT EXISTS signals
(
id INTEGER PRIMARY KEY AUTOINCREMENT,
coin TEXT,
direction TEXT,
entry REAL,
stop REAL,
tp1 REAL,
tp2 REAL,
score INTEGER,
timeframe TEXT,
strategy TEXT,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")


# Paper trades: each signal tracked until it hits target (WIN) or stop (LOSS).
cursor.execute("""
CREATE TABLE IF NOT EXISTS paper_trades
(
id INTEGER PRIMARY KEY AUTOINCREMENT,
coin TEXT,
direction TEXT,
entry REAL,
stop REAL,
tp1 REAL,
tp2 REAL,
score INTEGER,
timeframe TEXT,
strategy TEXT,
status TEXT DEFAULT 'OPEN',
opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
closed_at TIMESTAMP,
exit_price REAL,
pnl_pct REAL
)
""")


# Migration: add columns to databases created before these features existed.
for table in ("signals", "paper_trades"):
    for col in ("timeframe", "strategy"):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists


# Record of what was actually pinged to Telegram (for alert-on-change).
cursor.execute("""
CREATE TABLE IF NOT EXISTS alerts
(
id INTEGER PRIMARY KEY AUTOINCREMENT,
coin TEXT,
direction TEXT,
score INTEGER,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")


connection.commit()
connection.close()


print("Database ready (signals, paper_trades, alerts)")
