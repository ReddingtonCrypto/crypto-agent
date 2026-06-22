import sqlite3
from datetime import datetime, timedelta


def can_send_signal(coin, direction, price, confidence):

    conn = sqlite3.connect(
        "database/crypto.db"
    )

    cursor = conn.cursor()


    cursor.execute(
        """
        SELECT entry, score, created_at
        FROM signals
        WHERE coin=?
        AND direction=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            coin,
            direction
        )
    )


    last = cursor.fetchone()

    conn.close()


    if last is None:
        return True


    last_price = float(last[0])
    last_confidence = float(last[1])


    last_time = datetime.strptime(
        last[2],
        "%Y-%m-%d %H:%M:%S"
    )


    now = datetime.utcnow()

    age = now - last_time


    price_change = abs(
        price - last_price
    ) / last_price * 100


    confidence_change = abs(
        confidence - last_confidence
    )


    print("Last signal age:", age)
    print("Price change:", round(price_change,2), "%")
    print("Confidence change:", confidence_change)



    if age < timedelta(hours=24):

        if price_change < 2 and confidence_change < 15:

            print(
                "Duplicate signal blocked 🚫"
            )

            return False


    return True