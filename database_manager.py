import sqlite3
import os


os.makedirs("database", exist_ok=True)


connection = sqlite3.connect(
    "database/crypto.db"
)

cursor = connection.cursor()


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
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")


connection.commit()
connection.close()


print("Database created successfully 🚀")