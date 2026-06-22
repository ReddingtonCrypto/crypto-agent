import ccxt
import pandas as pd


exchange = ccxt.okx()


coins = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "ADA/USDT"
]


results = []


for coin in coins:

    bars = exchange.fetch_ohlcv(
        coin,
        timeframe="1h",
        limit=200
    )


    df = pd.DataFrame(
        bars,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )


    # ===== Indicators =====

    df["EMA20"] = df["close"].ewm(span=20).mean()
    df["EMA50"] = df["close"].ewm(span=50).mean()


    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss

    df["RSI"] = 100 - (100/(1+rs))


    # ATR

    tr1 = df["high"] - df["low"]
    tr2 = abs(df["high"] - df["close"].shift())
    tr3 = abs(df["low"] - df["close"].shift())

    df["ATR"] = pd.concat(
        [tr1,tr2,tr3],
        axis=1
    ).max(axis=1).rolling(14).mean()


    latest = df.iloc[-1]


    score = 0


    # ===== Trend =====

    ema_distance = abs(
        latest["EMA20"] - latest["EMA50"]
    ) / latest["close"] * 100


    if latest["EMA20"] > latest["EMA50"]:

        direction = "LONG"

        if ema_distance > 0.5:
            score += 30
        else:
            score += 15


    elif latest["EMA20"] < latest["EMA50"]:

        direction = "SHORT"

        if ema_distance > 0.5:
            score += 30
        else:
            score += 15


    else:

        direction="RANGE"

        score += 5



    # ===== RSI =====


    if direction == "LONG":

        if 40 <= latest.RSI <= 60:
            score += 25

        elif latest.RSI > 70:
            score -= 10


    elif direction == "SHORT":

        if 40 <= latest.RSI <= 60:
            score += 25

        elif latest.RSI < 30:
            score -= 10



    # ===== Volatility =====


    atr_percent = (
        latest.ATR / latest.close
    ) * 100


    if 0.5 < atr_percent < 5:

        score += 20

    else:

        score += 5



    # ===== Price alignment =====


    if direction=="LONG" and latest.close > latest.EMA20:
        score += 15


    elif direction=="SHORT" and latest.close < latest.EMA20:
        score += 15



    # Risk grade

    if score >= 80:
        risk = "A"

    elif score >= 65:
        risk = "B"

    else:
        risk = "C"



    results.append(
        {
        "coin":coin,
        "score":score,
        "direction":direction,
        "price":latest.close,
        "rsi":latest.RSI,
        "risk":risk
        }
    )



results = sorted(
    results,
    key=lambda x:x["score"],
    reverse=True
)



print("\n===== OPPORTUNITY RANKING v2 =====\n")


for r in results:

    print(
        f"{r['coin']} | {r['direction']} | Score: {r['score']} | Risk: {r['risk']} | RSI: {round(r['rsi'],2)} | Price: {round(r['price'],4)}"
    )