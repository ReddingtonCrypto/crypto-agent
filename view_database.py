import sqlite3


conn = sqlite3.connect(
    "database/crypto.db"
)

cursor = conn.cursor()


rows = cursor.execute(
    "SELECT * FROM signals"
)


print("\n===== SAVED SIGNALS =====")


for row in rows:
    print(row)


conn.close()