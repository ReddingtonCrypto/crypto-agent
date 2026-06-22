import sqlite3

conn = sqlite3.connect(
"database/crypto.db"
)

cursor=conn.cursor()

cursor.execute(
"DELETE FROM signals"
)

conn.commit()
conn.close()

print("Database cleared")